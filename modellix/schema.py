"""Bounded local-reference parsing with original combination semantics."""
import copy
import json

from jsonschema import Draft7Validator, FormatChecker
from jsonschema.validators import validator_for

from .cache import digest
from .client import slug_path
from .errors import ModellixError

MEDIA_TYPES = {
    "text-to-image": "Image", "image-to-image": "Image",
    "text-to-video": "Video", "image-to-video": "Video", "video-to-video": "Video",
    "text-to-speech": "Audio", "speech-to-speech": "Audio",
}


def tokens(pointer):
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ModellixError("Binding requires an absolute JSON Pointer.")
    return [p.replace("~1", "/").replace("~0", "~") for p in pointer[1:].split("/")]


def escape(value):
    return str(value).replace("~", "~0").replace("/", "~1")


def resolve(value, document, stack=(), depth=0):
    if depth > 32:
        raise ModellixError("Schema exceeds reference/nesting depth 32.")
    if isinstance(value, list):
        return [resolve(v, document, stack, depth + 1) for v in value]
    if not isinstance(value, dict):
        return value
    value = dict(value)
    if "$ref" in value:
        ref = value.pop("$ref")
        if not isinstance(ref, str) or not ref.startswith("#/"):
            raise ModellixError("External schema references are disabled.")
        if ref in stack:
            raise ModellixError("Circular schema reference: " + ref)
        target = document
        try:
            for part in tokens(ref[1:]):
                target = target[part]
        except (KeyError, TypeError):
            raise ModellixError("Unresolved local schema reference: " + ref) from None
        return {"allOf": [resolve(target, document, (*stack, ref), depth + 1),
                          resolve(value, document, stack, depth + 1)]} if value else resolve(
                              target, document, (*stack, ref), depth + 1)
    return {k: resolve(v, document, stack, depth + 1) for k, v in value.items()}


def dialect(value):
    if isinstance(value, list):
        return [dialect(v) for v in value]
    if not isinstance(value, dict):
        return value
    out = {k: dialect(v) for k, v in value.items() if k != "nullable"}
    if value.get("nullable"):
        # Preserve enum and all other constraints for non-null values.
        return {"anyOf": [out, {"type": "null"}]}
    for key in ("exclusiveMinimum", "exclusiveMaximum"):
        if isinstance(out.get(key), bool):
            enabled = out.pop(key)
            bound = key[9:].lower()
            if enabled and bound in out:
                out[key] = out.pop(bound)
    return out


def merge(left, right):
    out = copy.deepcopy(left)
    for k, v in right.items():
        if k == "properties":
            out[k] = {**out.get(k, {}), **v}
        elif k == "required":
            out[k] = list(dict.fromkeys(out.get(k, []) + v))
        else:
            out[k] = copy.deepcopy(v)
    return out


def active(schema, branches, path=""):
    """Only a form projection; validation always uses the full original schema."""
    out = {k: v for k, v in schema.items() if k not in ("allOf", "oneOf", "anyOf")}
    for index, child in enumerate(schema.get("allOf", [])):
        out = merge(out, active(child, branches, path + f"/allOf/{index}"))
    for kind in ("oneOf", "anyOf"):
        if kind in schema:
            choice = branches.get(path + "/" + kind, 0)
            if type(choice) is not int or not 0 <= choice < len(schema[kind]):
                raise ModellixError("Invalid schema branch selection at " + path)
            out = merge(out, active(schema[kind][choice], branches, path))
    return out


def media_kind(name, schema):
    key = name.lower()
    if schema.get("type") == "array":
        schema = schema.get("items", {})
    if schema.get("type") != "string":
        return None
    if key in ("url", "prompt_audio") and "audio" in schema.get("description", "").lower():
        return "AUDIO"
    if any(word in key for word in ("image", "frame", "mask")):
        return "IMAGE"
    if "video" in key:
        return "VIDEO"
    if "audio" in key:
        return "AUDIO"
    return None


class ModelSchema:
    def __init__(self, document, slug):
        slug_path(slug)
        if len(json.dumps(document).encode()) > 2 * 1024**2:
            raise ModellixError("Schema document exceeds 2 MiB.")
        post = document.get("post")
        if post is None:
            post = document.get("paths", {}).get("/api/v1/" + slug, {}).get("post")
            if post is None:
                post = document.get("paths", {}).get("/" + slug, {}).get("post")
        try:
            raw = post["requestBody"]["content"]["application/json"]["schema"]
        except (TypeError, KeyError):
            raise ModellixError("No request schema for selected model.") from None
        self.raw = document
        self.schema = resolve(raw, document)
        self.hash = digest(document)
        self.slug = slug
        normalized = dialect(self.schema)
        validator = validator_for(normalized, default=Draft7Validator)
        self.validator = validator(normalized, format_checker=FormatChecker())
        if self.count_fields(self.schema) > 500:
            raise ModellixError("Schema exceeds 500 form fields.")

    @staticmethod
    def count_fields(value):
        if isinstance(value, dict):
            return len(value.get("properties", {})) + sum(
                ModelSchema.count_fields(v) for v in value.values())
        if isinstance(value, list):
            return sum(ModelSchema.count_fields(v) for v in value)
        return 0

    def field(self, pointer, branches):
        current, path = self.schema, ""
        for part in tokens(pointer):
            current = active(current, branches, path)
            try:
                current = (current["items"] if current.get("type") == "array"
                           and part.isdigit() else current["properties"][part])
            except (KeyError, TypeError):
                raise ModellixError("Unknown or inactive bound field: " + pointer) from None
            path += "/" + escape(part)
        return active(current, branches, pointer)

    def validate(self, values):
        errors = sorted(self.validator.iter_errors(values), key=lambda e: list(map(str, e.path)))
        if errors:
            error = errors[0]
            # jsonschema messages may embed the complete submitted value. Only expose paths/rules.
            path = "/" + "/".join(map(str, error.absolute_path))
            raise ModellixError(f"Invalid model parameters at {path}: {error.validator} constraint. "
                                "Review the field description and selected branch.")
        # Some public schemas describe this rule without encoding it in JSON Schema.
        props = active(self.schema, {}, "").get("properties", {})
        if {"image", "video"}.issubset(props) and not values.get("image") and not values.get("video"):
            raise ModellixError("Provide at least one image or video reference.")


def unpack_parameters(text):
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        raise ModellixError("parameters_json is not valid JSON.") from None
    if not isinstance(payload, dict):
        raise ModellixError("Model parameters must be an object.")
    if "version" not in payload:
        return payload, {}  # Original headless raw-object format remains readable.
    if payload.get("version") != 1 or not isinstance(payload.get("values"), dict):
        raise ModellixError("Unsupported workflow parameter format. Expected version 1.")
    return payload["values"], payload.get("branches", {})


def set_pointer(values, pointer, value):
    parts = tokens(pointer)
    current = values
    for part in parts[:-1]:
        if isinstance(current, list):
            current = current[int(part)]
        else:
            current = current.setdefault(part, {})
    leaf = parts[-1]
    if isinstance(current, list):
        index = int(leaf)
        if index < len(current):
            raise ModellixError("Multiple sources for field " + pointer)
        if index != len(current):
            raise ModellixError("Array bindings must be contiguous and ordered.")
        current.append(value)
    elif leaf in current:
        raise ModellixError("Multiple sources for field " + pointer)
    else:
        current[leaf] = value
