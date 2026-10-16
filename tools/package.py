"""Create and inspect the actual custom_nodes directory archive, without Git/private files."""
import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FILES = ("__init__.py", "LICENSE", "README.md", "requirements.txt", "pyproject.toml")
FOLDERS = ("modellix", "web", "examples", "example_workflows")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "dist/modellix-comfyui.zip")
    args = parser.parse_args()
    paths = [ROOT / name for name in FILES]
    for name in FOLDERS:
        paths.extend(path for path in (ROOT / name).rglob("*") if path.is_file()
                     and "__pycache__" not in path.parts and path.suffix != ".pyc")
    for required in ("web/modellix.js", "example_workflows/text-to-image.json"):
        if not (ROOT / required).is_file():
            raise SystemExit("Missing directory-install resource: " + required)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(args.output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(paths):
            if not path.is_file():
                raise SystemExit("Missing install resource: " + path.name)
            archive.write(path, Path("modellix-comfyui") / path.relative_to(ROOT))
    with zipfile.ZipFile(args.output) as archive:
        names = archive.namelist()
        assert all("token" not in name and "apikey" not in name and ".agents" not in name
                   and "AGENTS.md" not in name and ".venv" not in name for name in names)
        assert "modellix-comfyui/__init__.py" in names
    print(f"Inspected {len(paths)} directory-install files: {args.output.name}")


if __name__ == "__main__":
    main()
