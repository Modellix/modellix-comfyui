"""Official Modellix ComfyUI extension. No network or credential I/O on import."""
WEB_DIRECTORY = "./web"
MINIMUM_COMFYUI_VERSION = "0.40.0"


def require_supported_host():
    """Check official host metadata before importing V3 nodes or registering routes."""
    from packaging.version import InvalidVersion, Version

    unknown = ("Cannot determine the ComfyUI version. Modellix requires ComfyUI >= "
               f"{MINIMUM_COMFYUI_VERSION}. Update or repair ComfyUI and restart.")
    try:
        from comfyui_version import __version__
    except ImportError:
        raise RuntimeError(unknown) from None
    try:
        current = Version(__version__)
    except (InvalidVersion, TypeError):
        raise RuntimeError(unknown) from None
    if current < Version(MINIMUM_COMFYUI_VERSION):
        raise RuntimeError(
            f"Modellix requires ComfyUI >= {MINIMUM_COMFYUI_VERSION}; detected {current}. "
            "Modellix nodes and routes are disabled. Update ComfyUI and restart.")


async def comfy_entrypoint():
    require_supported_host()
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
