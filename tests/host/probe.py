"""Run with the real ComfyUI interpreter and checkout on PYTHONPATH."""
# ruff: noqa: E402
import asyncio
import io as binary_io
from fractions import Fraction

import comfy.cli_args
import torch

comfy.cli_args.args.cpu = True
from comfy_api.latest import io
from comfy_api.latest._input_impl.video_types import VideoFromComponents, VideoFromFile
from comfy_api.latest._io import build_nested_inputs, get_finalized_class_inputs
from comfy_api.latest._util import VideoComponents, VideoContainer
from comfy_execution.graph import ExecutionBlocker


class Probe(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="ModellixHostProbe", is_input_list=True,
            inputs=[io.Autogrow.Input("images", io.Autogrow.TemplatePrefix(
                io.Image.Input("value"), prefix="slot", min=0, max=100), optional=True)],
            outputs=[io.Image.Output(is_output_list=True), io.String.Output()])

    @classmethod
    def execute(cls, images):
        return io.NodeOutput(ExecutionBlocker(None), "native")


async def main():
    Probe.GET_SCHEMA()
    # Actual V3 dynamic expansion validates the submitted connection as IMAGE.
    declaration, _, dynamic = get_finalized_class_inputs(Probe.INPUT_TYPES(), {"images.slot0": ["source", 0]})
    assert declaration["optional"]["images.slot0"][0] == "IMAGE"
    image = torch.zeros((1, 32, 32, 3))
    nested = build_nested_inputs({"images.slot0": [image]}, dynamic, input_is_list=True)
    assert nested["images"]["slot0"][0] is image
    audio = {"waveform": torch.zeros((1, 1, 16000)), "sample_rate": 16000}
    video = VideoFromComponents(VideoComponents(images=image, frame_rate=Fraction(1), audio=audio))
    buffer = binary_io.BytesIO()
    video.save_to(buffer, format=VideoContainer.MP4)
    buffer.seek(0)
    restored = VideoFromFile(buffer)
    assert restored.get_dimensions() == (32, 32)
    assert restored.get_duration() > 0
    result = Probe.execute({"slot0": [image]})
    assert isinstance(result[0], ExecutionBlocker) and result[1] == "native"
    print("Real host native VIDEO/AUDIO and per-output blocker probe passed.")


if __name__ == "__main__":
    asyncio.run(main())
