import json
from pathlib import Path

import pytest

from modellix.errors import ModellixError
from modellix.media import url_list
from modellix.schema import ModelSchema, active, dialect, media_kind, resolve, set_pointer


def schema(value):
    return ModelSchema({"post": {"requestBody": {"content": {
        "application/json": {"schema": value}}}}}, "vendor/model.2")


def test_oneof_preserves_required_and_selection():
    model = schema({"oneOf": [
        {"type": "object", "properties": {"first_frame_image": {"type": "string"}},
         "required": ["first_frame_image"], "additionalProperties": False},
        {"type": "object", "properties": {"reference_images": {"type": "array"}},
         "required": ["reference_images"], "additionalProperties": False},
    ]})
    model.validate({"first_frame_image": "https://example.test/a.png"})
    model.validate({"reference_images": ["https://example.test/a.png"]})
    with pytest.raises(ModellixError):
        model.validate({})
    assert "reference_images" in active(model.schema, {"/oneOf": 1})["properties"]


def test_ref_nullable_and_external_rejection():
    value = resolve({"$ref": "#/defs/value"}, {"defs": {"value": {"type": "integer"}}})
    assert value == {"type": "integer"}
    with pytest.raises(ModellixError, match="External"):
        resolve({"$ref": "https://evil.test/schema"}, {})
    with pytest.raises(ModellixError, match="Circular"):
        resolve({"$ref": "#/defs/value"}, {"defs": {"value": {"$ref": "#/defs/value"}}})
    assert dialect({"type": "integer", "nullable": True})["anyOf"][1] == {"type": "null"}


@pytest.mark.parametrize(("name", "kind"), [("image", "IMAGE"), ("reference_images", "IMAGE"),
    ("first_frame_image", "IMAGE"), ("last_frame_image", "IMAGE"), ("video_url", "VIDEO"),
    ("audio_urls", "AUDIO"), ("prompt_audio", "AUDIO"), ("url", "AUDIO"), ("prompt", None)])
def test_media_field_semantics(name, kind):
    assert media_kind(name, {"type": "string", "description": "Audio URL" if name in
                            ("url", "prompt_audio") else ""}) == kind


def test_source_conflict_does_not_override_values():
    values = {"image": "already entered"}
    with pytest.raises(ModellixError, match="Multiple sources"):
        set_pointer(values, "/image", "connected input")


def test_independent_choices_nested_in_allof():
    model = schema({"type": "object", "allOf": [{"oneOf": [
        {"properties": {"a": {"type": "string"}}, "required": ["a"]},
        {"properties": {"b": {"type": "string"}}, "required": ["b"]},
    ]}, {"oneOf": [
        {"properties": {"c": {"type": "string"}}, "required": ["c"]},
        {"properties": {"d": {"type": "string"}}, "required": ["d"]},
    ]}]})
    branches = {"/allOf/0/oneOf": 1, "/allOf/1/oneOf": 0}
    assert set(active(model.schema, branches)["properties"]) == {"b", "c"}
    assert model.field("/b", branches)["type"] == "string"
    model.validate({"b": "selected", "c": "selected"})


def test_url_list_preserves_order_and_duplicates():
    items = ["https://example.test/a?x=1,2", "https://example.test/b", "https://example.test/b"]
    assert url_list({"version": 1, "items": items}) == items
    with pytest.raises(ModellixError):
        url_list({"version": 1, "items": ["file:///secret"]})


@pytest.mark.parametrize("path", list((Path(__file__).parents[1] / "fixtures").glob("*.json")))
def test_public_schema_fixture(path):
    document = json.loads(path.read_text(encoding="utf-8"))
    ModelSchema(document, path.stem.replace("__", "/"))
