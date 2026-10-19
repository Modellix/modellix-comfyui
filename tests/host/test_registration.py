"""The supported host must still load the extension through its real loader."""
import asyncio
from pathlib import Path

import nodes
from app.assets.manager import default_asset_manager
from server import PromptServer


async def test_supported_host_registers_all_nodes_and_routes(monkeypatch):
    monkeypatch.setattr(PromptServer, "instance", None, raising=False)
    server = PromptServer(asyncio.get_running_loop(), default_asset_manager())
    for name in ("NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "EXTENSION_WEB_DIRS",
                 "LOADED_MODULE_DIRS"):
        monkeypatch.setattr(nodes, name, dict(getattr(nodes, name)))
    root = Path(__file__).resolve().parents[2]
    assert await nodes.load_custom_node(str(root))
    expected = {"ModellixGenerate", "ModellixGetTask", "ModellixUploadMedia", "ModellixURLList"}
    assert expected <= nodes.NODE_CLASS_MAPPINGS.keys()
    assert all(nodes.NODE_CLASS_MAPPINGS[name].GET_SCHEMA().category == "Modellix/Media"
               for name in expected)
    paths = {route.path for route in server.routes}
    assert {"/modellix/session", "/modellix/credentials", "/modellix/models",
            "/modellix/schema", "/modellix/cache/clean", "/modellix/results/{filename}"} <= paths
