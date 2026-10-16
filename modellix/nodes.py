"""V3 schemas declare every socket for normal host type validation."""
from comfy_api.latest import io
from comfy_execution.graph import ExecutionBlocker

from . import runtime
from .tasks import GROUPS, single


def media_outputs():
    return [io.Image.Output("images", is_output_list=True),
            io.Video.Output("video", is_output_list=True),
            io.Audio.Output("audio", is_output_list=True), io.String.Output("task_id")]


def node_result(result, status_output=False):
    outputs = result["outputs"]
    args = [outputs.get(kind) or ExecutionBlocker(None) for kind in ("image", "video", "audio")]
    args.append(result["task_id"])
    if status_output:
        args.append(result["status"])
    return io.NodeOutput(*args, ui={"modellix": [{k: result[k] for k in
                        ("task_id", "status", "manifest", "requests", "cached", "billing") if k in result}]})


def socket_inputs():
    types = {"IMAGE": io.Image, "VIDEO": io.Video, "AUDIO": io.Audio, "STRING": io.String,
             "INT": io.Int, "FLOAT": io.Float, "BOOLEAN": io.Boolean,
             "MODELLIX_URL_LIST": io.Custom("MODELLIX_URL_LIST")}
    return [io.Autogrow.Input(name, io.Autogrow.TemplatePrefix(
        types[kind].Input("value"), prefix="slot", min=0, max=100), optional=True)
        for name, kind in GROUPS.items()]


class ModellixGenerate(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(
            node_id="ModellixGenerate", display_name="Modellix Generate", category="Modellix/Media",
            description="Generate native media through Modellix. Queue executes this output node. "
                        "Use Mute to disable it. New Attempt changes the saved request nonce.",
            is_output_node=True, is_input_list=True, not_idempotent=True,
            hidden=[io.Hidden.unique_id],
            inputs=[
                io.String.Input("model_slug", default=""),
                io.String.Input("parameters_json", default='{"version":1,"values":{},"branches":{}}',
                                multiline=True),
                io.String.Input("schema_hash", default="", advanced=True),
                io.String.Input("bindings_json", default='{"version":1,"bindings":[]}', advanced=True),
                io.String.Input("node_instance_id", default="", advanced=True),
                io.String.Input("run_nonce", default="initial", advanced=True),
                io.Combo.Input("mode", options=["Wait", "Submit only"], default="Wait"),
                io.String.Input("resume_task_id", default="", advanced=True),
                io.Int.Input("timeout", default=1800, min=1, max=86400, advanced=True),
                *socket_inputs(),
            ], outputs=media_outputs())

    @classmethod
    def fingerprint_inputs(cls, **kwargs):
        return runtime.fingerprint()

    @classmethod
    async def execute(cls, **kwargs):
        groups = {key: kwargs.pop(key, {}) for key in GROUPS}
        controls = {key: single(value, key) for key, value in kwargs.items()}
        result = await runtime.cancellable(runtime.run_generate(
            cls.hidden.unique_id, groups=groups, **controls))
        return node_result(result)
