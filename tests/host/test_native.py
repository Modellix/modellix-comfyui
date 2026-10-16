import asyncio
import time
from fractions import Fraction

import pytest
import torch
from comfy_api.latest import io
from comfy_api.latest._input_impl.video_types import VideoFromComponents
from comfy_api.latest._io import get_finalized_class_inputs
from comfy_api.latest._util import VideoComponents
from comfy_execution.graph import ExecutionBlocker

from modellix import runtime
from modellix.media import decode, encode
from modellix.nodes import ModellixGenerate, node_result


def test_native_media_round_trips_preserve_shapes_rates_and_audio_track(tmp_path):
    image = torch.rand((2, 32, 32, 3))
    blobs = encode("IMAGE", image)
    assert len(blobs) == 2
    picture = tmp_path / "image.png"
    picture.write_bytes(blobs[0].content)
    assert decode(picture, "image").shape == (1, 32, 32, 3)
    samples = torch.zeros((1, 2, 16000))
    audio = {"waveform": samples, "sample_rate": 16000}
    sound = tmp_path / "sound.wav"
    sound.write_bytes(encode("AUDIO", audio)[0].content)
    restored = decode(sound, "audio")
    assert restored["sample_rate"] == 16000 and restored["waveform"].shape == samples.shape
    video = VideoFromComponents(VideoComponents(images=image, frame_rate=Fraction(2), audio=audio))
    movie = tmp_path / "video.mp4"
    movie.write_bytes(encode("VIDEO", video)[0].content)
    native = decode(movie, "video")
    assert native.get_dimensions() == (32, 32)
    assert native.get_components().audio is not None


def test_individual_media_outputs_are_blocked_without_fake_media():
    import execution
    ModellixGenerate.GET_SCHEMA()
    image = torch.zeros((1, 32, 32, 3))
    result = node_result({"outputs": {"image": [image]}, "task_id": "test-task",
                          "status": "success", "manifest": []})
    output, _, _ = execution.get_output_from_returns([result], ModellixGenerate)
    assert output[0][0] is image
    assert isinstance(output[1][0], ExecutionBlocker)
    assert isinstance(output[2][0], ExecutionBlocker)
    assert output[3] == ["test-task"]


async def test_real_host_list_execution_calls_generate_once(monkeypatch):
    import execution
    calls = []
    image = torch.zeros((1, 32, 32, 3))

    async def fake_run(node_id, **kwargs):
        calls.append(kwargs)
        return {"outputs": {"image": [image]}, "task_id": "test-task",
                "status": "success", "manifest": []}

    monkeypatch.setattr(runtime, "run_generate", fake_run)
    controls = {"model_slug": "v/m", "parameters_json": "{}", "schema_hash": "",
        "bindings_json": '{"version":1,"bindings":[]}', "node_instance_id": "test",
        "run_nonce": "initial", "mode": "Wait", "resume_task_id": "", "timeout": 2}
    values = {key: [value] for key, value in controls.items()}
    values["images.slot0"] = [image, image]
    _, _, v3 = get_finalized_class_inputs(ModellixGenerate.INPUT_TYPES(), values)
    v3["hidden_inputs"] = {}
    returns = await execution._async_map_node_over_list(
        "test-prompt", "test-node", ModellixGenerate, values, "execute", v3_data=v3)
    await asyncio.gather(*(value for value in returns if isinstance(value, asyncio.Task)))
    assert len(calls) == 1
    assert len(calls[0]["groups"]["images"]["slot0"]) == 2


async def test_declared_autogrow_connection_uses_host_type_validation(monkeypatch):
    import execution
    import nodes

    class ImageSource(io.ComfyNode):
        @classmethod
        def define_schema(cls):
            return io.Schema(node_id="ModellixTestImageSource", outputs=[io.Image.Output()])

        @classmethod
        def execute(cls):
            return io.NodeOutput(torch.zeros((1, 32, 32, 3)))

    monkeypatch.setitem(nodes.NODE_CLASS_MAPPINGS, "ModellixTestImageSource", ImageSource)
    monkeypatch.setitem(nodes.NODE_CLASS_MAPPINGS, "ModellixGenerate", ModellixGenerate)
    controls = {"model_slug": "v/m", "parameters_json": "{}", "schema_hash": "",
        "bindings_json": '{"version":1,"bindings":[]}', "node_instance_id": "test",
        "run_nonce": "initial", "mode": "Wait", "resume_task_id": "", "timeout": 2}
    prompt = {"1": {"class_type": "ModellixTestImageSource", "inputs": {}},
        "2": {"class_type": "ModellixGenerate", "inputs": {**controls, "images.slot0": ["1", 0]}}}
    assert (await execution.validate_prompt("test", prompt, None))[0] is True
    prompt["2"]["inputs"].pop("images.slot0")
    prompt["2"]["inputs"]["videos.slot0"] = ["1", 0]
    assert (await execution.validate_prompt("test", prompt, None))[0] is False


async def test_host_cancel_exits_network_wait_under_two_seconds():
    import comfy.model_management as management
    finished = asyncio.Event()

    async def network_wait():
        try:
            await asyncio.sleep(60)
        finally:
            finished.set()

    started = time.monotonic()

    async def interrupt():
        await asyncio.sleep(0.15)
        management.interrupt_current_processing(True)

    job = asyncio.create_task(interrupt())
    try:
        with pytest.raises(management.InterruptProcessingException):
            await runtime.cancellable(network_wait())
    finally:
        management.interrupt_current_processing(False)
        await job
    assert finished.is_set() and time.monotonic() - started < 2


async def test_multiple_different_sized_results_remain_native_lists(tmp_path):
    from PIL import Image

    from modellix.cache import Store
    from modellix.client import Deadline
    from modellix.media import Results

    class Client:
        class credential:
            context = "test-context"

        async def download(self, url, path, budget, **kwargs):
            size = (32, 48) if url.endswith("first") else (48, 32)
            Image.new("RGB", size, "blue").save(path)
            return "image/png", path.stat().st_size

    results = Results(tmp_path / "results", Store(tmp_path / "state"))
    outputs, manifest = await results.materialize(Client(), {"task_id": "test", "result": {
        "resources": [{"type": "image", "url": "https://example.test/first"},
                      {"type": "image", "url": "https://example.test/second"}]}}, Deadline(2))
    assert [tuple(image.shape) for image in outputs["image"]] == [(1, 48, 32, 3), (1, 32, 48, 3)]
    assert len(manifest) == 2
