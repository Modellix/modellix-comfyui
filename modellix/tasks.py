"""Paid attempts survive timeouts, lost responses and process restarts."""
import asyncio
import copy
import json
import time

from .cache import digest
from .catalog import Catalog
from .client import Deadline, task_failure
from .errors import ModellixError
from .media import encode, upload_blob, url_list
from .schema import MEDIA_TYPES, media_kind, set_pointer, tokens, unpack_parameters

GROUPS = {"images": "IMAGE", "videos": "VIDEO", "audios": "AUDIO", "strings": "STRING",
          "integers": "INT", "numbers": "FLOAT", "booleans": "BOOLEAN",
          "url_lists": "MODELLIX_URL_LIST"}


def single(values, name):
    if not isinstance(values, list) or len(values) != 1:
        raise ModellixError(name + " requires exactly one value; implicit paid batches are disabled.")
    return values[0]


def binding_entries(text):
    try:
        body = json.loads(text)
    except (TypeError, ValueError):
        raise ModellixError("Invalid bindings_json.") from None
    if body.get("version") != 1 or not isinstance(body.get("bindings"), list):
        raise ModellixError("Unsupported binding format. Expected version 1.")
    return body["bindings"]


async def bind_inputs(model, values, branches, bindings, groups):
    values, uploads, used = copy.deepcopy(values), [], set()
    for entry in bindings:
        socket, pointer = entry.get("socket", ""), entry.get("pointer", "")
        if socket in used:
            raise ModellixError("Duplicate socket binding.")
        used.add(socket)
        try:
            group, name = socket.split(".")
            if not name.startswith("slot") or not 0 <= int(name[4:]) < 100:
                raise ValueError
            kind = GROUPS[group]
        except (ValueError, KeyError):
            raise ModellixError("Invalid typed socket: " + socket) from None
        field = model.field(pointer, branches)
        data = groups.get(group, {}).get(name)
        if data is None:
            raise ModellixError("Bound input is disconnected: " + pointer)
        schema_type = field.get("type")
        is_array = schema_type == "array"
        if kind in ("IMAGE", "VIDEO", "AUDIO"):
            expected = media_kind(tokens(pointer)[-1], field)
            if kind != expected:
                raise ModellixError("Media type does not match schema field: " + pointer)
            blobs = []
            for item in data:
                blobs.extend(await asyncio.to_thread(encode, kind, item))
            if not is_array and len(blobs) != 1:
                raise ModellixError("Scalar media field rejects multiple inputs: " + pointer)
            result = ["https://media.invalid/" + blob.hash for blob in blobs]
            uploads.append((pointer, blobs, is_array))
        elif kind == "MODELLIX_URL_LIST":
            if not is_array or field.get("items", {}).get("type") != "string":
                raise ModellixError("URL List requires an array-of-strings field: " + pointer)
            result = [url for item in data for url in url_list(item)]
        else:
            target = field.get("items", {}) if is_array else field
            expected = {"string": "STRING", "integer": "INT", "number": "FLOAT",
                        "boolean": "BOOLEAN"}.get(target.get("type"))
            if kind != expected:
                raise ModellixError("Socket type does not match schema field: " + pointer)
            result = list(data)
        set_pointer(values, pointer, result if is_array else single(result, pointer))
    # Every live connection must be mapped; hidden stale inputs must never be silently ignored.
    for group, inputs in groups.items():
        for name in inputs:
            if group + "." + name not in used:
                raise ModellixError("Connected socket has no active field binding.")
    model.validate(values)
    return values, uploads


def replace_pointer(values, pointer, value):
    current = values
    parts = tokens(pointer)
    for part in parts[:-1]:
        current = current[int(part)] if isinstance(current, list) else current[part]
    if isinstance(current, list):
        current[int(parts[-1])] = value
    else:
        current[parts[-1]] = value


class Engine:
    def __init__(self, client, store, results):
        self.client, self.store, self.results = client, store, results
        self.attempt_key = None

    def progress(self, stage, tid="", **extra):
        if self.attempt_key and tid:
            record = self.store.get("attempt", self.attempt_key) or {}
            state = {"pending": "waiting", "processing": "waiting", "downloading": "downloading",
                     "ready": "ready"}.get(stage)
            if state:
                self.store.put("attempt", self.attempt_key,
                               {**record, "task_id": tid, "state": state, "updated": time.time()})
        self.client.progress(stage=stage, task_id=tid, **extra)

    async def get_task(self, tid, *, wait=True, budget=None, model=None):
        budget = budget or Deadline()
        while True:
            data = await self.client.query(tid, budget, wait=wait)
            actual_model = data.get("model_slug") or data.get("model_id") or data.get("model")
            if model and actual_model and isinstance(actual_model, str) and actual_model != model:
                raise ModellixError("Recovered task model does not match selected model.", task_id=tid)
            self.progress(data["status"], tid)
            if data["status"] in ("failed", "canceled"):
                raise task_failure(data, self.client.credential.key)
            if data["status"] == "success":
                self.progress("downloading", tid)
                outputs, manifest = await self.results.materialize(self.client, data, budget)
                self.progress("ready", tid)
                billing = data.get("billing") or {}
                return {"task_id": tid, "status": "success", "outputs": outputs,
                        "manifest": manifest, "requests": dict(self.client.counts),
                        "billing": {k: billing[k] for k in ("status", "amount", "currency") if k in billing}}
            if not wait:
                return {"task_id": tid, "status": data["status"], "outputs": {}, "manifest": [],
                        "requests": dict(self.client.counts)}
            await budget.sleep(0.2)

    async def generate(self, *, model_slug, schema_hash, parameters_json, bindings_json,
                       node_instance_id, run_nonce, mode="Wait", resume_task_id="",
                       timeout=1800, groups=None):
        budget = Deadline(timeout)
        if resume_task_id:
            return await self.get_task(resume_task_id, budget=budget, model=model_slug or None)
        if not node_instance_id or not run_nonce:
            raise ModellixError("Stable node_instance_id and run_nonce are required. "
                                "Use the editor or supply them in your API prompt.")
        if mode not in ("Wait", "Submit only"):
            raise ModellixError("Unknown execution mode.")
        catalog = Catalog(self.store, self.client)
        models = await catalog.models(budget=budget)
        model_entry = next((m for m in models if m.get("slug") == model_slug), None)
        if not model_entry or model_entry.get("type") not in MEDIA_TYPES:
            raise ModellixError("Selected model is absent from your media catalog or has unsupported output.")
        schema = await catalog.schema(model_slug, refresh=True, budget=budget)
        if schema_hash and schema.hash != schema_hash:
            raise ModellixError("Model schema changed. Refresh and review fields before a paid submission.")
        values, branches = unpack_parameters(parameters_json)
        bindings = binding_entries(bindings_json)
        values, uploads = await bind_inputs(schema, values, branches, bindings, groups or {})
        key = digest([self.client.credential.context, node_instance_id, model_slug,
                      schema.hash, values, run_nonce])
        intent = digest([self.client.credential.context, node_instance_id, model_slug, values, run_nonce])
        async with self.store.lock("intent:" + intent, budget):
            reviewed = self.store.get("intent", intent)
            if reviewed and reviewed["schema_hash"] != schema.hash:
                if not schema_hash:
                    raise ModellixError("Schema changed since this attempt. Supply the reviewed schema_hash "
                                        "or explicitly select New Attempt; automatic replay is disabled.")
                # Reviewing new metadata does not authorize replaying an unchanged paid intent.
                key = reviewed["attempt"]
                if self.store.get("attempt", key) is None:
                    raise ModellixError("Attempt record damaged or missing; automatic replay is disabled.")
            else:
                self.store.put("intent", intent, {"schema_hash": schema.hash, "attempt": key})
        async with self.store.lock("attempt:" + key, budget):
            self.attempt_key = key
            previous = self.store.get("attempt", key)
            if previous and previous.get("state") in ("submitting", "outcome_unknown"):
                raise ModellixError("Submission outcome unknown. Check task history; this attempt "
                                    "cannot replay. Choose New Attempt only after checking.", unknown=True)
            tid = previous.get("task_id") if previous else None
            if tid:
                self.progress("resuming", tid, cached=True)
            else:
                record = {"state": "prepared", "model": model_slug, "created": time.time()}
                self.store.put("attempt", key, record)
                for pointer, blobs, is_array in uploads:
                    self.progress("uploading")
                    urls = [(await upload_blob(blob, self.client, self.store, budget, run_nonce))["url"]
                            for blob in blobs]
                    replace_pointer(values, pointer, urls if is_array else urls[0])
                schema.validate(values)
                budget.remaining()
                record["state"] = "submitting"
                self.store.put("attempt", key, record)
                self.progress("submitting")
                try:
                    tid = await self.client.submit(model_slug, values, budget)
                except BaseException as error:
                    tid = getattr(error, "task_id", None)
                    record.update(state="submitted" if tid else "outcome_unknown", task_id=tid)
                    self.store.put("attempt", key, record)
                    if tid:
                        self.progress("submission_error", tid)
                    raise
                record.update(state="submitted", task_id=tid)
                self.store.put("attempt", key, record)
                self.progress("submitted", tid)
            if mode == "Submit only":
                return {"task_id": tid, "status": "submitted", "outputs": {}, "manifest": [],
                        "requests": dict(self.client.counts), "cached": bool(previous)}
            try:
                result = await self.get_task(tid, budget=budget, model=model_slug)
                result["cached"] = bool(previous)
                self.store.put("attempt", key, {**(previous or {}), "task_id": tid,
                                                "model": model_slug, "state": "ready"})
                return result
            except ModellixError as error:
                record = self.store.get("attempt", key) or {}
                self.store.put("attempt", key, {**record, "task_id": tid, "state": "failed"})
                if error.task_id:
                    raise
                raise ModellixError(str(error), task_id=tid) from None
            except asyncio.CancelledError:
                record = self.store.get("attempt", key) or {}
                self.store.put("attempt", key, {**record, "task_id": tid, "state": "canceled"})
                raise
