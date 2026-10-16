from comfy_api.latest import io

from .media import url_list


class ModellixURLList(io.ComfyNode):
    @classmethod
    def define_schema(cls):
        return io.Schema(node_id="ModellixURLList", display_name="Modellix URL List",
            category="Modellix/Media", inputs=[
                io.String.Input("urls", multiline=True, default=""),
                io.Combo.Input("order", options=["Append", "Prepend"], default="Append"),
                io.Custom("MODELLIX_URL_LIST").Input("existing", optional=True),
                io.Custom("MODELLIX_URL_LIST").Input("additional", optional=True),
            ], outputs=[io.Custom("MODELLIX_URL_LIST").Output("url_list")])

    @classmethod
    def execute(cls, urls, order, existing=None, additional=None):
        lines = [line.strip() for line in urls.splitlines() if line.strip()]
        before = url_list(existing) if existing else []
        after = url_list(additional) if additional else []
        items = lines + before + after if order == "Prepend" else before + after + lines
        value = {"version": 1, "items": items}
        url_list(value)
        return io.NodeOutput(value)
