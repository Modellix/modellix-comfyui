"""Host tests run only with an actual ComfyUI checkout on PYTHONPATH."""
import pytest

comfy_args = pytest.importorskip("comfy.cli_args", reason="Real ComfyUI host not installed")
comfy_args.args.cpu = True
