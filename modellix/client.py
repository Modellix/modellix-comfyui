"""Single-shot mutations and deadline-bounded safe reads."""
import asyncio
import ipaddress
import json
import random
import re
import shutil
import socket
import time
import uuid
import weakref
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import quote, urljoin, urlsplit

import aiohttp
from filelock import FileLock

from .errors import ModellixError, api_error, safe_text

API = "https://api.modellix.ai"
WEB = "https://www.modellix.ai"
_query_queues = weakref.WeakKeyDictionary()


def slug_path(slug):
    if (not isinstance(slug, str) or not re.fullmatch(r"[\w.-]+/[\w.-]+", slug)
        or any(part in (".", "..") for part in slug.split("/"))):
        raise ModellixError("Invalid provider/model slug.")
    return "/".join(quote(part, safe="") for part in slug.split("/"))


def task_id(value):
    if (not isinstance(value, str) or value in (".", "..")
        or not re.fullmatch(r"[A-Za-z0-9_:@.+~-]{1,200}", value)):
        raise ModellixError("Invalid task ID.")
    return value


class Deadline:
    def __init__(self, seconds=1800):
        self.end = time.monotonic() + seconds

    def remaining(self):
        value = self.end - time.monotonic()
        if value <= 0:
            raise ModellixError("Local deadline expired. Resume the existing task ID; "
                                "no new generation was submitted.")
        return value

    async def sleep(self, seconds):
        await asyncio.sleep(min(seconds, self.remaining()))
        self.remaining()


class PublicResolver(aiohttp.abc.AbstractResolver):
    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await asyncio.get_running_loop().getaddrinfo(
            host, port, type=socket.SOCK_STREAM, family=family)
        result = []
        for fam, _, proto, _, address in records:
            if not ipaddress.ip_address(address[0]).is_global:
                raise ModellixError("Private/local resource addresses are not allowed.")
            result.append({"hostname": host, "host": address[0], "port": port,
                           "family": fam, "proto": proto, "flags": socket.AI_NUMERICHOST})
        return result

    async def close(self):
        pass


class Client:
    def __init__(self, credential, *, session=None, api=API, web=WEB, progress=None):
        self.credential = credential
        self.api, self.web = api, web
        self.session = session
        self.owned = session is None
        self.progress = progress or (lambda **_: None)
        self.counts = {"generate": 0, "upload": 0, "query": 0, "download": 0}

    async def __aenter__(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(trust_env=False)
        return self

    async def __aexit__(self, *_):
        if self.owned:
            await self.session.close()

    async def request(self, method, path, budget, *, public=False, **kwargs):
        url = (self.web if public else self.api) + path
        headers = {} if public else {"Authorization": "Bearer " + self.credential.key}
        for attempt in range(4 if method == "GET" else 1):
            retry_after = None
            try:
                async with self.session.request(
                    method, url, headers=headers, allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=min(60, budget.remaining())),
                    **kwargs,
                ) as response:
                    chunks, size = [], 0
                    async for chunk in response.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > 2 * 1024 * 1024:
                            raise ModellixError("API document exceeds the 2 MiB limit.",
                                                unknown=method == "POST")
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    try:
                        body = json.loads(raw)
                    except (ValueError, UnicodeError):
                        body = {}
                    if not isinstance(body, dict):
                        body = {}
                    data = body.get("data")
                    tid = data.get("task_id") if isinstance(data, dict) else None
                    try:
                        tid = task_id(tid) if tid else None
                    except ModellixError:
                        tid = None
                    if response.status == 200 and body and body.get("code", 0) == 0:
                        return body
                    error = api_error(response.status, tid, method == "POST" and not tid)
                    if method != "GET" or response.status not in (429, 500, 502, 503, 504):
                        raise error
                    value = response.headers.get("Retry-After", "")
                    if value.isdigit():
                        retry_after = min(int(value), 60)
                    elif value:
                        try:
                            retry_after = max(0, min(60, parsedate_to_datetime(value).timestamp()
                                                     - time.time()))
                        except (ValueError, TypeError, OverflowError):
                            pass
            except (aiohttp.ClientError, asyncio.TimeoutError):
                error = ModellixError(
                    "Network request failed. " + ("Submission outcome unknown. Check task "
                    "history before choosing New Attempt." if method == "POST" else
                    "Resume using the existing task ID."), unknown=method == "POST")
                if method == "POST":
                    raise error from None
            if attempt == 3:
                raise error
            await budget.sleep(retry_after if retry_after is not None else
                               2 ** attempt + random.random() * 0.2)

    async def models(self, budget):
        body = await self.request("GET", "/api/v1/models", budget)
        if not isinstance(body.get("models"), list):
            raise ModellixError("Invalid catalog response: expected models[].")
        return body["models"]

    async def schema(self, slug, budget):
        return await self.request("GET", "/models/" + slug_path(slug) + "/api_schema",
                                  budget, public=True)

    async def submit(self, slug, parameters, budget):
        self.counts["generate"] += 1
        response = await self.request("POST", "/api/v1/" + slug_path(slug), budget,
                                      json=parameters)
        try:
            return task_id(response["data"]["task_id"])
        except (KeyError, TypeError, ModellixError):
            raise ModellixError("Submission outcome unknown: response has no valid task ID. "
                                "Check task history; no automatic replay.", unknown=True) from None

    async def query(self, tid, budget, wait=False):
        tid = task_id(tid)
        self.counts["query"] += 1
        params = {"wait": "true", "timeout": "5"} if wait else {}
        queues = _query_queues.setdefault(asyncio.get_running_loop(), {})
        queue = queues.setdefault(self.credential.context, asyncio.Semaphore(2))
        try:
            await asyncio.wait_for(queue.acquire(), budget.remaining())
        except asyncio.TimeoutError:
            raise ModellixError("Query queue deadline expired. Resume the task ID.", task_id=tid) from None
        try:
            body = await self.request("GET", "/api/v1/tasks/" + quote(tid, safe=""), budget,
                                      params=params)
        finally:
            queue.release()
        data = body.get("data", {})
        if data.get("task_id") != tid:
            raise ModellixError("Task identity mismatch.", task_id=tid)
        if data.get("status") not in ("pending", "processing", "success", "failed", "canceled"):
            raise ModellixError("Unknown task status.", task_id=tid)
        return data

    async def upload(self, blob, budget):
        self.counts["upload"] += 1
        form = aiohttp.FormData()
        form.add_field("file", blob.content, filename=blob.filename, content_type=blob.mime)
        body = await self.request("POST", "/api/v1/media/files", budget, data=form)
        data = body.get("data", {})
        if not isinstance(data.get("url"), str) or not data.get("file_id"):
            raise ModellixError("Upload outcome unknown: missing file metadata.", unknown=True)
        return data

    async def download(self, url, destination, budget, *, limit=2 * 1024**3):
        """Separate credential-free session, validate every redirect and DNS address."""
        destination = Path(destination)
        part = destination.with_name(uuid.uuid4().hex + ".part")
        self.counts["download"] += 1
        for attempt in range(4):
            lease = FileLock(str(part) + ".lock")
            lease.acquire(timeout=0)
            try:
                async with aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(resolver=PublicResolver(), use_dns_cache=False),
                    trust_env=False,
                ) as session:
                    current = url
                    for _ in range(6):
                        parsed = urlsplit(current)
                        if (parsed.scheme != "https" or not parsed.hostname or
                            parsed.username is not None or parsed.password is not None):
                            raise ModellixError("Result must be a public HTTPS URL.")
                        try:
                            ip = ipaddress.ip_address(parsed.hostname)
                        except ValueError:
                            ip = None
                        if ip and not ip.is_global:
                            raise ModellixError("Private/local resource URL rejected.")
                        async with session.get(current, allow_redirects=False,
                            timeout=aiohttp.ClientTimeout(total=min(300, budget.remaining()),
                                                         sock_read=30)) as response:
                            if response.status in (301, 302, 303, 307, 308):
                                current = urljoin(current, response.headers.get("Location", ""))
                                continue
                            if response.status != 200:
                                raise api_error(response.status)
                            if response.content_length and response.content_length > limit:
                                raise ModellixError("Resource exceeds download size limit.")
                            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                            required = min(limit, response.content_length or 64 * 1024**2)
                            if shutil.disk_usage(destination.parent).free < required + 16 * 1024**2:
                                raise ModellixError("Insufficient free disk space for this result.")
                            size = 0
                            with part.open("wb") as file:
                                async for chunk in response.content.iter_chunked(256 * 1024):
                                    budget.remaining()
                                    size += len(chunk)
                                    if size > limit:
                                        raise ModellixError("Resource exceeds download size limit.")
                                    file.write(chunk)
                            if not size:
                                raise ModellixError("Empty downloaded resource.")
                            part.replace(destination)
                            return response.headers.get("Content-Type", ""), size
                    raise ModellixError("Too many resource redirects.")
            except ModellixError as error:
                if error.status not in (429, 500, 502, 503, 504) or attempt == 3:
                    raise
                await budget.sleep(2 ** attempt)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                if attempt == 3:
                    raise ModellixError("Resource download failed. Resume the task ID.") from None
                await budget.sleep(2 ** attempt)
            finally:
                part.unlink(missing_ok=True)
                lease.release()
                Path(str(part) + ".lock").unlink(missing_ok=True)


def resources(data):
    result = data.get("result") or {}
    items = result.get("resources", data.get("resources", []))
    if not isinstance(items, list) or not items:
        raise ModellixError("Successful task has no resources.", task_id=data.get("task_id"))
    if len(items) > 100:
        raise ModellixError("Task has more than 100 resources.", task_id=data.get("task_id"))
    return items


def task_failure(data, secret=""):
    return ModellixError("Upstream task " + data["status"] + ": " +
                         safe_text(data.get("error") or "No media returned.", secret),
                         task_id=data.get("task_id"))
