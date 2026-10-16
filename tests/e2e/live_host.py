"""Explicitly billed acceptance against an already running local ComfyUI host.

Never embeds a key in a prompt. The host uses its configured credential.
Reruns preserve node instance IDs/nonces and recover the original attempts.
"""
import argparse
import asyncio
import json
import os
import time
from pathlib import Path

import aiohttp


def generate(name, slug, values, bindings=None, branches=None, **sockets):
    return {"class_type": "ModellixGenerate", "inputs": {
        "model_slug": slug, "parameters_json": json.dumps({"version": 1, "values": values,
        "branches": branches or {}}), "schema_hash": "", "bindings_json": json.dumps({"version": 1,
        "bindings": bindings or []}), "node_instance_id": "acceptance-v1-" + name,
        "run_nonce": "initial", "mode": "Wait", "resume_task_id": "", "timeout": 1800,
        **sockets,
    }}


def binding(socket, pointer):
    return {"socket": socket, "pointer": pointer}


def cases(seed_task):
    source = {"class_type": "ModellixGetTask", "inputs": {
        "task_id": seed_task, "mode": "Wait", "timeout": 1800}}
    t2v = generate("text-video", "bytedance/seedance-2.0-mini-t2v", {
        "prompt": "A small red toy sailboat on a calm lake, gentle ripples, no people or text.",
        "duration": 4, "resolution": "480p", "ratio": "1:1", "generate_audio": False})
    tts = generate("speech", "minimax/speech-2.8-turbo", {
        "text": "Welcome to Modellix. This is a synthetic voice sample for testing a media workflow. "
                "A small wooden sailboat floats quietly on the blue lake. Gentle waves move toward "
                "the shore as sunlight crosses the water. Thank you for listening to this example.",
        "voice_id": "English_expressive_narrator", "format": "wav", "sample_rate": 16000,
        "channel": 1})
    save_video = {"class_type": "SaveVideo", "inputs": {"video": ["2", 1],
        "filename_prefix": "modellix-acceptance/video", "format": "auto", "codec": "auto"}}
    return {
        "image-edit": {"1": source, "2": generate("image-edit", "google/nano-banana-2-lite-edit",
            {"prompt": "Change the red hull of the boat to bright yellow; keep the lake and composition."},
            [binding("images.slot0", "/image")], **{"images.slot0": ["1", 0]}),
            "3": {"class_type": "PreviewImage", "inputs": {"images": ["2", 0]}}},
        "text-video": {"2": t2v, "3": save_video},
        "first-last-video": {"1": source, "2": generate("first-last", "bytedance/seedance-2.0-mini-i2v",
            {"prompt": "The toy sailboat gently rocks on the water. Camera stays fixed.", "duration": 4,
             "resolution": "480p", "ratio": "1:1", "generate_audio": False},
            [binding("images.slot0", "/first_frame_image"), binding("images.slot1", "/last_frame_image")],
            **{"images.slot0": ["1", 0], "images.slot1": ["1", 0]}), "3": save_video},
        "reference-video": {"1": source, "2": generate("reference", "bytedance/seedance-2.0-mini-i2v",
            {"prompt": "[Image 1] A toy sailboat floating on a lake, gentle waves.", "duration": 4,
             "resolution": "480p", "ratio": "1:1", "generate_audio": False},
            [binding("images.slot0", "/reference_images")], branches={"/oneOf": 1},
            **{"images.slot0": ["1", 0]}), "3": save_video},
        "video-edit": {"1": t2v, "2": generate("video-edit", "alibaba/wan2.7-videoedit",
            {"prompt": "Transform the scene into a watercolor animation.", "duration": 2,
             "resolution": "720P", "audio_setting": "origin"},
            [binding("videos.slot0", "/video_url")], **{"videos.slot0": ["1", 1]}), "3": save_video},
        "speech": {"2": tts, "3": {"class_type": "PreviewAudio", "inputs": {"audio": ["2", 2]}}},
        "speech-transform": {"1": tts, "2": generate("speech-transform", "alibaba/cosyvoice-clone",
            {"model": "cosyvoice-v3.5-flash", "text": "This is a synthetic speech transformation "
             "test using a generated reference voice.", "language_hint": "en", "format": "wav",
             "sample_rate": 16000}, [binding("audios.slot0", "/url")],
            **{"audios.slot0": ["1", 2]}), "3": {"class_type": "PreviewAudio", "inputs": {"audio": ["2", 2]}}},
        "upload-url-list": {"1": source,
            "2": {"class_type": "ModellixUploadMedia", "inputs": {"order": "IMAGE,VIDEO,AUDIO",
                  "image": ["1", 0]}},
            "3": {"class_type": "ModellixURLList", "inputs": {"urls": "", "order": "Append",
                  "existing": ["2", 1]}},
            "4": generate("url-reference-edit", "google/nano-banana-2-lite-edit",
                {"prompt": "Make the sky a soft sunset pink, retaining the toy sailboat."},
                [binding("url_lists.slot0", "/image")], **{"url_lists.slot0": ["3", 0]}),
            "5": {"class_type": "PreviewImage", "inputs": {"images": ["4", 0]}}},
    }


async def queue(session, prompt):
    async with session.post("http://localhost:8188/prompt", json={"prompt": prompt}) as response:
        body = await response.json()
        if response.status != 200:
            return {"passed": False, "stage": "host-validation", "errors": body.get("node_errors", {})}
        prompt_id = body["prompt_id"]
    end = time.monotonic() + 1900
    while time.monotonic() < end:
        async with session.get("http://localhost:8188/history/" + prompt_id) as response:
            history = (await response.json()).get(prompt_id)
        if history:
            status = history["status"]
            entries = [entry for result in history.get("outputs", {}).values()
                       for entry in result.get("modellix", [])]
            errors = [message[1].get("exception_message", "Host error") for message in
                      status.get("messages", []) if message[0] == "execution_error"]
            return {"passed": status["status_str"] == "success", "prompt_id": prompt_id,
                    "results": entries, "errors": errors}
        await asyncio.sleep(2)
    return {"passed": False, "prompt_id": prompt_id, "stage": "local-deadline"}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--allow-billed", action="store_true")
    parser.add_argument("--seed-task", required=True)
    parser.add_argument("--case", action="append")
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    if not args.allow_billed or os.environ.get("MODELLIX_ALLOW_BILLED_E2E") != "1":
        raise SystemExit("Explicit billed acceptance flag/environment required.")
    report = json.loads(args.report.read_text(encoding="utf-8")) if args.report.exists() else {}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        for name, prompt in cases(args.seed_task).items():
            if args.case and name not in args.case:
                continue
            if report.get(name, {}).get("passed"):
                print(json.dumps({"case": name, "reused_acceptance": True}), flush=True)
                continue
            started = time.monotonic()
            report[name] = await queue(session, prompt)
            report[name]["elapsed_seconds"] = round(time.monotonic() - started, 1)
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
            print(json.dumps({"case": name, **report[name]}), flush=True)


if __name__ == "__main__":
    asyncio.run(main())
