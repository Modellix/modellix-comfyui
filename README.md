# modellix-comfyui

Official Modellix custom nodes for ComfyUI.

**Status: development setup. Generation nodes are not implemented yet.** This repository currently contains project configuration and development tooling. Installing it does not add a working generation node.

## Planned experience

- Select a Modellix image or video model in one `Modellix Generate` node.
- Configure a Modellix API key in ComfyUI Settings or with `MODELLIX_API_KEY`.
- Connect native images; the node uploads inputs, submits once, waits, and downloads results.
- Connect native `IMAGE` and `VIDEO` outputs to ComfyUI preview and save nodes.
- Preserve task IDs for recovery without submitting another paid task.

The integration calls the [Modellix media REST API](https://docs.modellix.ai/ways-to-use/api) directly. It does not require the Modellix CLI, an Agent Plugin, or a ComfyUI Partner account.

## Development

The initial compatibility target is ComfyUI 0.40.0 and Python 3.10 or later. Use the Python environment that runs ComfyUI for host integration checks. Standalone development tools can use an isolated virtual environment:

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pip check
.\.venv\Scripts\python -m ruff check .
```

Runtime dependencies are maintained in `requirements.txt`; project metadata reads that file. ComfyUI supplies torch, NumPy, Pillow, and native video support. Dependency installation must use the intended interpreter; importing the future node package must never install packages automatically.

Implementation will add mocked protocol tests and example workflows before functional releases. The current setup checks do not constitute generation or ComfyUI compatibility acceptance.

## Credentials and API origins

Create a key in the [Modellix Console](https://www.modellix.ai/console/api-key). The runtime environment variable is `MODELLIX_API_KEY`. Never put a key in a workflow, source file, fixture, screenshot, command argument, or issue.

Media API requests use `https://api.modellix.ai`. Public model schemas use `https://www.modellix.ai/models/{provider}/{model}/api_schema` without forwarding an API key. Model slugs come from the authenticated model catalog.

Generation creates paid tasks. A lost response must not trigger an automatic retry of the generation POST. Recover by an existing task ID or request history.

## Distribution

The repository uses the MIT license. Comfy Registry publication will be configured after the actual publisher identity and compatibility checks are available. No functional release or Registry publication is currently claimed.
