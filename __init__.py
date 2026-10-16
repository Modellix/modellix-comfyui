"""Official Modellix ComfyUI extension. No network or credential I/O on import."""
WEB_DIRECTORY = "./web"


async def comfy_entrypoint():
    from comfy_api.latest import ComfyExtension

    from .modellix.helper_nodes import ModellixURLList
    from .modellix.nodes import ModellixGenerate
    from .modellix.routes import register
    from .modellix.task_node import ModellixGetTask
    from .modellix.upload_node import ModellixUploadMedia

    class ModellixExtension(ComfyExtension):
        async def get_node_list(self):
            return [ModellixGenerate, ModellixGetTask, ModellixUploadMedia, ModellixURLList]

    register()
    return ModellixExtension()
