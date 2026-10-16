import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_ui_templates_and_api_examples_have_one_source():
    for path in (ROOT / "examples/api").glob("*.json"):
        api = json.loads(path.read_text(encoding="utf-8-sig"))
        ui = json.loads((ROOT / "examples/workflows" / path.name).read_text(encoding="utf-8-sig"))
        template = json.loads((ROOT / "example_workflows" / path.name).read_text(encoding="utf-8-sig"))
        assert ui == template
        nodes = {str(node["id"]): node for node in ui["nodes"]}
        assert set(nodes) == set(api)
        for node_id, node in api.items():
            assert nodes[node_id]["type"] == node["class_type"]
            if node["class_type"] != "ModellixGenerate":
                continue
            values = nodes[node_id]["widgets_values_named"]
            for name in ("parameters_json", "bindings_json"):
                assert json.loads(values[name]) == json.loads(node["inputs"][name])
            assert values["node_instance_id"] == node["inputs"]["node_instance_id"]
            assert values["run_nonce"] == node["inputs"]["run_nonce"]
