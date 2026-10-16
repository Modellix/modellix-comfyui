"""Native ComfyUI media codecs, loaded lazily in the host interpreter."""
import asyncio
import hashlib
import io
import os
import time
import uuid
import wave
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock, Timeout

from .cache import digest
from .errors import ModellixError

UPLOAD_LIMIT = 200 * 1024**2
PINNED = set()  # Conservative lifetime: host cache may retain any returned VIDEO.
PIN_LEASES = {}


def pin(path):
    if path not in PIN_LEASES:
        lease = FileLock(str(path) + f".lease-{os.getpid()}")
        with FileLock(str(path.parent / ".lifecycle.lock")):
            lease.acquire(timeout=0)
            PIN_LEASES[path] = lease
    PINNED.add(path)


def evict(path):
    leases = []
    try:
        with FileLock(str(path.parent / ".lifecycle.lock")).acquire(timeout=0):
            for marker in path.parent.glob(path.name + ".lease-*"):
                lease = FileLock(str(marker))
                lease.acquire(timeout=0)
                leases.append(lease)
            size = path.stat().st_size
            path.unlink()
            return size
    except (Timeout, OSError):
        return 0
    finally:
        for lease in leases:
            lease.release()


def file_hash(path):
    checksum = hashlib.sha256()
    with Path(path).open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


@dataclass(repr=False)
class Blob:
    content: bytes
    mime: str
    filename: str

    @property
    def hash(self):
        return hashlib.sha256(self.content).hexdigest()

    def check(self):
        if not self.content or len(self.content) > UPLOAD_LIMIT:
            raise ModellixError("Upload must contain 1 byte to 200 MiB.")
        return self


def encode(kind, item):
    import numpy as np
    import torch
    if kind == "IMAGE":
        from PIL import Image
        if not isinstance(item, torch.Tensor) or item.ndim != 4 or item.shape[-1] not in (3, 4):
            raise ModellixError("IMAGE must be a [B,H,W,3/4] tensor.")
        if item.shape[1] * item.shape[2] > 64_000_000 or not torch.isfinite(item).all():
            raise ModellixError("Invalid image values or image exceeds 64 MP.")
        result = []
        for image in item.detach().cpu():
            buffer = io.BytesIO()
            Image.fromarray((image.clamp(0, 1).numpy() * 255).round().astype(np.uint8)).save(
                buffer, format="PNG")
            result.append(Blob(buffer.getvalue(), "image/png", "input.png").check())
        return result
    if kind == "AUDIO":
        if not isinstance(item, dict) or "waveform" not in item:
            raise ModellixError("AUDIO must contain waveform and sample_rate.")
        waveform, rate = item["waveform"], item.get("sample_rate")
        if (not isinstance(waveform, torch.Tensor) or waveform.ndim != 3 or
            waveform.shape[1] not in (1, 2) or not waveform.shape[2] or
            not isinstance(rate, int) or not 8000 <= rate <= 192000 or
            not torch.isfinite(waveform).all()):
            raise ModellixError("Invalid AUDIO shape, rate, channels or samples.")
        result = []
        for audio in waveform.detach().cpu():
            buffer = io.BytesIO()
            with wave.open(buffer, "wb") as file:
                file.setnchannels(audio.shape[0])
                file.setsampwidth(2)
                file.setframerate(rate)
                samples = (audio.clamp(-1, 1).t().numpy() * 32767).round().astype("<i2")
                file.writeframes(samples.tobytes())
            result.append(Blob(buffer.getvalue(), "audio/wav", "input.wav").check())
        return result
    if kind == "VIDEO":
        if not callable(getattr(item, "save_to", None)):
            raise ModellixError("Connect a native VIDEO, such as Load Video.")
        from comfy_api.latest._util import VideoContainer
        buffer = io.BytesIO()
        item.save_to(buffer, format=VideoContainer.MP4, metadata=None)
        return [Blob(buffer.getvalue(), "video/mp4", "input.mp4").check()]
    raise ModellixError("Unsupported media input type.")


async def upload_blob(blob, client, store, budget, nonce="initial"):
    key = digest([client.credential.context, blob.hash])
    async with store.lock("upload:" + key, budget):
        previous = store.get("upload", key)
        if previous:
            if previous.get("state") != "ready" and previous.get("nonce", "initial") == nonce:
                raise ModellixError("Prior upload outcome unknown. Check the File API quota/history "
                                    "before explicitly choosing New Upload Attempt.", unknown=True)
            if previous.get("state") == "ready":
                return previous["data"]
        budget.remaining()
        store.put("upload", key, {"state": "submitting", "nonce": nonce})
        try:
            data = await client.upload(blob, budget)
        except BaseException:
            store.put("upload", key, {"state": "outcome_unknown", "nonce": nonce})
            raise
        ttl = 86400
        expires = data.get("expires_at")
        if isinstance(expires, (int, float)):
            ttl = max(1, min(ttl, expires / (1000 if expires > 1e11 else 1) - time.time()))
        store.put("upload", key, {"state": "ready", "data": data}, ttl)
        return data


def decode(path, kind):
    import torch
    if kind == "image":
        import numpy as np
        from PIL import Image, ImageOps
        with Image.open(path) as image:
            if image.width * image.height > 64_000_000:
                raise ModellixError("Result image exceeds 64 MP.")
            image = ImageOps.exif_transpose(image).convert("RGB")
            return torch.from_numpy(np.asarray(image).copy()).float().div_(255).unsqueeze(0)
    if kind == "video":
        import av
        from comfy_api.latest._input_impl.video_types import VideoFromFile
        with av.open(str(path)) as container:
            if not container.streams.video:
                raise ModellixError("Result is not a decodable video.")
        return VideoFromFile(str(path))
    if kind == "audio":
        from comfy_extras.nodes_audio import load
        samples, rate = load(str(path))
        if samples.ndim != 2 or samples.shape[0] not in (1, 2) or not samples.shape[1]:
            raise ModellixError("Result audio has invalid channels or no samples.")
        return {"waveform": samples.unsqueeze(0), "sample_rate": rate}
    raise ModellixError("Unknown result media type.")


class Results:
    def __init__(self, root, store):
        self.root, self.store = Path(root), store
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.capacity = int(os.environ.get("MODELLIX_CACHE_BYTES", 10 * 1024**3))
        self.limit = int(os.environ.get("MODELLIX_RESOURCE_BYTES", 2 * 1024**3))
        self.clean_parts()

    def clean_parts(self):
        for path in self.root.glob("*.part"):
            if path.is_symlink():
                continue
            lease = FileLock(str(path) + ".lock")
            try:
                with lease.acquire(timeout=0):
                    path.unlink(missing_ok=True)
            except (Timeout, OSError):
                pass

    def clean(self):
        entries = self.store.records("result")
        total = sum(path.stat().st_size for path in self.root.iterdir() if path.is_file()
                    and path.suffix not in (".lease", ".lock", ".part"))
        for _, record, _ in sorted(entries, key=lambda e: e[1].get("created", 0)):
            path = Path(record["path"])
            if (path in PINNED or not path.is_file() or self.root.resolve() not in
                path.resolve().parents):
                continue
            if total <= self.capacity and time.time() - record.get("created", 0) < 7 * 86400:
                continue
            total -= evict(path)
        return total

    async def materialize(self, client, data, budget):
        async with self.store.lock("result-cache", budget):
            return await self._materialize(client, data, budget)

    async def _materialize(self, client, data, budget):
        from .client import resources
        self.clean()
        outputs, manifest, size = {"image": [], "video": [], "audio": []}, [], 0
        for index, resource in enumerate(resources(data)):
            if not isinstance(resource, dict):
                raise ModellixError("Malformed task resource.", task_id=data["task_id"])
            kind = resource.get("type")
            url = resource.get("url")
            if kind not in ("image", "video", "audio", "json", "subtitle", "text") or not url:
                raise ModellixError("Unknown or missing resource type/URL.", task_id=data["task_id"])
            key = digest([client.credential.context, data["task_id"], index])
            record = self.store.get("result", key)
            path = Path(record["path"]) if record else None
            if path and self.root.resolve() in path.resolve().parents and path.is_file():
                pin(path)
            valid = (path and self.root.resolve() in path.resolve().parents and path.is_file()
                     and await asyncio.to_thread(file_hash, path) == record.get("checksum"))
            if not valid:
                available = self.capacity - self.clean()
                if available <= 0:
                    raise ModellixError("Media cache full; active files are protected. Restart "
                                        "the host to release pins or increase MODELLIX_CACHE_BYTES.")
                extension = {"image": ".png", "video": ".mp4", "audio": ".wav",
                             "json": ".json", "subtitle": ".srt", "text": ".txt"}[kind]
                path = self.root / (uuid.uuid4().hex + extension)
                pin(path)
                mime, count = await client.download(url, path, budget, limit=min(
                    self.limit, available, 4 * 1024**3 - size))
                if kind in outputs and mime and not mime.startswith((kind + "/", "application/octet-stream")):
                    path.unlink(missing_ok=True)
                    raise ModellixError("Result MIME does not match its media type.")
                record = {"path": str(path), "kind": kind, "created": time.time(),
                          "checksum": await asyncio.to_thread(file_hash, path)}
                self.store.put("result", key, record)
            size += path.stat().st_size
            if size > 4 * 1024**3:
                raise ModellixError("Task exceeds 4 GiB total download budget.")
            if kind in outputs:
                try:
                    outputs[kind].append(await asyncio.to_thread(decode, path, kind))
                except Exception as error:
                    # A checksum proves local byte stability, not that the download decodes.
                    # Keep the expired index for safe cleanup, but refetch on task recovery.
                    self.store.put("result", key, {**record, "created": 0}, ttl=-1)
                    if isinstance(error, ModellixError):
                        raise
                    raise ModellixError("Media decoding failed. Resume the existing task ID.",
                                        task_id=data["task_id"]) from None
            pin(path)
            manifest.append({"filename": path.name, "type": kind})
        if not any(outputs.values()):
            raise ModellixError("Task has no primary image/video/audio result.", task_id=data["task_id"])
        return outputs, manifest


def url_list(value):
    if not isinstance(value, dict) or value.get("version") != 1 or not isinstance(value.get("items"), list):
        raise ModellixError("Invalid MODELLIX_URL_LIST format (expected version 1).")
    items = value["items"]
    if len(items) > 100:
        raise ModellixError("URL list exceeds 100 items.")
    for url in items:
        from urllib.parse import urlsplit
        parsed = urlsplit(url) if isinstance(url, str) else None
        if (not parsed or parsed.scheme != "https" or not parsed.hostname or
            parsed.username is not None or parsed.password is not None):
            raise ModellixError("Each reference URL must be an absolute HTTPS URL.")
    return list(items)
