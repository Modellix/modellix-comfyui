"""Generate API examples; UI workflows are serialized by the real ComfyUI frontend."""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("live_host", ROOT / "tests/e2e/live_host.py")
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


def main():
    output = ROOT / "examples/api"
    output.mkdir(parents=True, exist_ok=True)
    seed = live.generate("text-to-image", "google/nano-banana-2-lite", {
        "prompt": "A small red wooden sailboat floating on a calm turquoise lake, simple clean "
                  "illustration, no people, no text.", "aspectRatio": "1:1"})
    examples = live.cases("REPLACE_WITH_EXISTING_TASK_ID")
    # Executable recommended examples create their own source, never ship real task IDs.
    for prompt in examples.values():
        for key, node in list(prompt.items()):
            if node["class_type"] == "ModellixGetTask":
                prompt[key] = json.loads(json.dumps(seed))
    examples["text-to-image"] = {"1": seed, "2": {"class_type": "PreviewImage", "inputs": {"images": ["1", 0]}}}
    submitted = json.loads(json.dumps(seed))
    submitted["inputs"]["mode"] = "Submit only"
    examples["submit-and-get-task"] = {"1": submitted, "2": {"class_type": "ModellixGetTask",
        "inputs": {"task_id": ["1", 3], "mode": "Wait", "timeout": 1800}},
        "3": {"class_type": "PreviewImage", "inputs": {"images": ["2", 0]}}}
    for name, prompt in examples.items():
        for key, node in prompt.items():
            if node["class_type"] == "ModellixGenerate":
                node["inputs"]["node_instance_id"] = f"example-v1-{name}-{key}"
        (output / (name + ".json")).write_text(json.dumps(prompt, indent=2) + "\n", encoding="utf-8")
    print(f"Generated {len(examples)} credential-free API examples.")


if __name__ == "__main__":
    main()
