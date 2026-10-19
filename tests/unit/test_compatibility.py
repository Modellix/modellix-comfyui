"""Version metadata fixtures do not stand in for a real ComfyUI host."""
import importlib.util
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def extension():
    spec = importlib.util.spec_from_file_location(
        "modellix_compatibility_test", ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("version", ["0.39.2", "0.40.0rc1", "0.40.0.dev1"])
async def test_old_host_is_rejected_before_loading_host_apis(monkeypatch, extension, version):
    monkeypatch.setitem(sys.modules, "comfyui_version", SimpleNamespace(__version__=version))
    with pytest.raises(RuntimeError, match=r"requires ComfyUI >= 0\.40\.0.*detected " + version):
        await extension.comfy_entrypoint()
    assert not any(name.startswith(extension.__name__ + ".modellix.") for name in sys.modules)


@pytest.mark.parametrize("version", ["0.40.0", "v0.40.0", "0.40.0+local", "0.41.0.dev1", "1.0.0"])
def test_supported_version_metadata_passes_gate(monkeypatch, extension, version):
    monkeypatch.setitem(sys.modules, "comfyui_version", SimpleNamespace(__version__=version))
    extension.require_supported_host()


@pytest.mark.parametrize("version", ["", "not-a-version", None, 40])
async def test_unreadable_version_fails_with_upgrade_guidance(monkeypatch, extension, version):
    monkeypatch.setitem(sys.modules, "comfyui_version", SimpleNamespace(__version__=version))
    with pytest.raises(RuntimeError, match=r"Cannot determine.*ComfyUI.*0\.40\.0.*restart"):
        await extension.comfy_entrypoint()


async def test_missing_version_module_is_reported_before_host_api_import(monkeypatch, extension):
    monkeypatch.setitem(sys.modules, "comfyui_version", None)
    with pytest.raises(RuntimeError, match=r"Cannot determine.*ComfyUI.*0\.40\.0.*restart"):
        await extension.comfy_entrypoint()


def test_runtime_minimum_matches_registry_metadata(extension):
    metadata = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    minimum = re.search(r'requires-comfyui\s*=\s*">=([^" ]+)"', metadata).group(1)
    assert extension.MINIMUM_COMFYUI_VERSION == minimum
