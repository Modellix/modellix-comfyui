"""Lazy host services and prompt-local cancellation supervision."""
import asyncio
import os
import time

from .auth import auth
from .cache import Store
from .client import Client
from .media import Results
from .tasks import Engine

_store = None


def store():
    global _store
    if _store is None:
        import folder_paths
        _store = Store(os.environ.get("MODELLIX_STATE_DIRECTORY") or
                       os.path.join(folder_paths.get_user_directory(), "modellix"))
    return _store


def results():
    import folder_paths
    return Results(os.environ.get("MODELLIX_RESULT_DIRECTORY") or
                   os.path.join(folder_paths.get_output_directory(), "modellix"), store())


def fingerprint():
    try:
        context = auth.snapshot(store()).context
        return (store().public_fingerprint(context), auth.revision)
    except Exception:
        return ("unconfigured", auth.revision)


def progress(node_id):
    started = time.monotonic()

    def emit(**payload):
        from server import PromptServer
        PromptServer.instance.send_sync("modellix.status", {
            "node": node_id, "elapsed": round(time.monotonic() - started, 1), **payload})
    return emit


async def cancellable(coroutine):
    import comfy.model_management
    job = asyncio.create_task(coroutine)
    try:
        while not job.done():
            comfy.model_management.throw_exception_if_processing_interrupted()
            await asyncio.wait({job}, timeout=0.1)
        return await job
    finally:
        if not job.done():
            job.cancel()
            try:
                await job
            except asyncio.CancelledError:
                pass


async def run_generate(node_id, **kwargs):
    credential = auth.snapshot(store())
    async with Client(credential, progress=progress(node_id)) as client:
        return await Engine(client, store(), results()).generate(**kwargs)


async def run_task(node_id, tid, wait, timeout):
    from .client import Deadline
    credential = auth.snapshot(store())
    async with Client(credential, progress=progress(node_id)) as client:
        return await Engine(client, store(), results()).get_task(
            tid, wait=wait, budget=Deadline(timeout))
