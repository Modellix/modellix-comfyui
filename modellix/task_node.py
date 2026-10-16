from comfy_api.latest import io

from . import runtime
from .nodes import media_outputs, node_result
from .tasks import single


class ModellixGetTask(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="ModellixGetTask", display_name="Modellix Get Task",
            category="Modellix/Media", is_input_list=True, is_output_node=True,
            hidden=[io.Hidden.unique_id], inputs=[
                io.String.Input("task_id", default=""),
                io.Combo.Input("mode", options=["Wait", "Snapshot"], default="Wait"),
                io.Int.Input("timeout", default=1800, min=1, max=86400),
            ], outputs=[*media_outputs(), io.String.Output("status")])

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return float("nan")

    @classmethod
    async def execute(cls, task_id, mode, timeout):
        result = await runtime.cancellable(runtime.run_task(
            cls.hidden.unique_id, single(task_id, "task_id"),
            single(mode, "mode") == "Wait", single(timeout, "timeout")))
        return node_result(result, status_output=True)
