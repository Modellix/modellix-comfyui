import asyncio

from comfy_api.latest import io

from . import runtime
from .auth import auth
from .client import Client, Deadline
from .errors import ModellixError
from .media import encode, upload_blob
from .tasks import single


async def upload(node_id, order, groups, nonce):
    credential = auth.snapshot(runtime.store())
    entries = []
    async with Client(credential, progress=runtime.progress(node_id)) as client:
        budget = Deadline()
        for kind in order:
            for item in groups.get(kind, []):
                for blob in await asyncio.to_thread(encode, kind, item):
                    client.progress(stage="uploading")
                    data = await upload_blob(blob, client, runtime.store(), budget, nonce)
                    entries.append((data["url"], data["file_id"], kind.lower()))
    if not entries:
        raise ModellixError("Connect at least one IMAGE, VIDEO or AUDIO input.")
    urls, ids, kinds = map(list, zip(*entries, strict=True))
    return io.NodeOutput(urls, {"version": 1, "items": urls}, ids, kinds,
                         ui={"modellix": [{"status": "uploaded", "count": len(urls)}]})


class ModellixUploadMedia(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="ModellixUploadMedia", display_name="Modellix Upload Media",
            category="Modellix/Media", is_input_list=True, is_output_node=True,
            hidden=[io.Hidden.unique_id], inputs=[
                io.Combo.Input("order", options=["IMAGE,VIDEO,AUDIO", "VIDEO,IMAGE,AUDIO",
                    "AUDIO,IMAGE,VIDEO", "IMAGE,AUDIO,VIDEO", "VIDEO,AUDIO,IMAGE", "AUDIO,VIDEO,IMAGE"]),
                io.Image.Input("image", optional=True), io.Video.Input("video", optional=True),
                io.Audio.Input("audio", optional=True),
                io.String.Input("run_nonce", default="initial", optional=True, advanced=True),
            ], outputs=[io.String.Output("urls", is_output_list=True),
                io.Custom("MODELLIX_URL_LIST").Output("url_list"),
                io.String.Output("file_ids", is_output_list=True),
                io.String.Output("media_types", is_output_list=True)])

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return runtime.fingerprint()

    @classmethod
    async def execute(cls, order, image=None, video=None, audio=None, run_nonce=None):
        return await runtime.cancellable(upload(cls.hidden.unique_id,
            single(order, "order").split(","),
            {"IMAGE": image or [], "VIDEO": video or [], "AUDIO": audio or []},
            (single(run_nonce, "run_nonce") or "initial") if run_nonce else "initial"))
