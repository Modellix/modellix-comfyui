import asyncio
import copy
import json

import pytest

from modellix.auth import Credential
from modellix.cache import Store
from modellix.errors import ModellixError
from modellix.schema import ModelSchema
from modellix.tasks import Engine

DOCUMENT = {"post": {"requestBody": {"content": {"application/json": {"schema": {
    "type": "object", "properties": {"prompt": {"type": "string"}}, "required": ["prompt"],
}}}}}}


class FakeClient:
    credential = Credential("fake-key", "fake-context")

    def __init__(self):
        self.posts = self.queries = 0
        self.counts = {"generate": 0, "upload": 0, "query": 0, "download": 0}
        self.fail = False
        self.states = ["success"]

    def progress(self, **kwargs):
        pass

    async def models(self, budget):
        return [{"slug": "vendor/model.2", "type": "text-to-image"}]

    async def schema(self, slug, budget):
        return DOCUMENT

    async def submit(self, slug, values, budget):
        self.posts += 1
        await asyncio.sleep(0)
        if self.fail:
            raise ModellixError("Unknown outcome", unknown=True)
        return "task-1"

    async def query(self, tid, budget, wait=False):
        self.queries += 1
        return {"task_id": tid, "status": self.states.pop(0) if len(self.states) > 1 else self.states[0]}


class FakeResults:
    async def materialize(self, client, data, budget):
        return {"image": ["native-test-sentinel"]}, []


def controls(**extra):
    return {"model_slug": "vendor/model.2", "schema_hash": "", "parameters_json":
        json.dumps({"prompt": "Test"}), "bindings_json": '{"version":1,"bindings":[]}',
        "node_instance_id": "test-node", "run_nonce": "initial", **extra}


async def test_stable_nonce_modes_and_restart_reuse_one_paid_task(tmp_path):
    client = FakeClient()
    engine = Engine(client, Store(tmp_path), FakeResults())
    submitted = await engine.generate(**controls(mode="Submit only"))
    assert submitted["task_id"] == "task-1" and client.posts == 1 and client.queries == 0
    assert submitted["outputs"] == {}
    restarted = Engine(client, Store(tmp_path), FakeResults())
    await restarted.generate(**controls())
    await restarted.generate(**controls())
    assert client.posts == 1

    await restarted.generate(**controls(run_nonce="explicit-new-attempt"))
    assert client.posts == 2


async def test_concurrent_same_attempt_submits_once(tmp_path):
    client = FakeClient()
    engine = Engine(client, Store(tmp_path), FakeResults())
    await asyncio.gather(engine.generate(**controls()), engine.generate(**controls()))
    assert client.posts == 1


async def test_unknown_attempt_never_replays_after_restart(tmp_path):
    client = FakeClient()
    client.fail = True
    with pytest.raises(ModellixError):
        await Engine(client, Store(tmp_path), FakeResults()).generate(**controls())
    with pytest.raises(ModellixError, match="cannot replay"):
        await Engine(client, Store(tmp_path), FakeResults()).generate(**controls())
    assert client.posts == 1


async def test_resume_skips_schema_upload_and_generation(tmp_path):
    client = FakeClient()
    await Engine(client, Store(tmp_path), FakeResults()).generate(
        **controls(model_slug="", resume_task_id="existing-task"))
    assert client.posts == 0 and client.queries == 1


async def test_snapshot_refreshes_pending_without_media(tmp_path):
    client = FakeClient()
    client.states = ["pending", "processing", "success"]
    engine = Engine(client, Store(tmp_path), FakeResults())
    for status in ["pending", "processing", "success"]:
        result = await engine.get_task("task-1", wait=False)
        assert result["status"] == status
        assert bool(result["outputs"]) == (status == "success")
    assert client.posts == 0 and client.queries == 3


async def test_model_identity_mismatch_is_rejected_before_download(tmp_path):
    class WrongModel(FakeClient):
        async def query(self, tid, budget, wait=False):
            return {"task_id": tid, "status": "success", "model_id": "another/model"}

    with pytest.raises(ModellixError, match="model does not match"):
        await Engine(WrongModel(), Store(tmp_path), FakeResults()).get_task("task-1", model="vendor/model.2")


@pytest.mark.parametrize("status", ["failed", "canceled"])
async def test_terminal_failure_never_creates_a_replacement_task(tmp_path, status):
    client = FakeClient()
    client.states = [status]
    engine = Engine(client, Store(tmp_path), FakeResults())
    for _ in range(2):
        with pytest.raises(ModellixError):
            await engine.generate(**controls())
    assert client.posts == 1


async def test_cancel_during_submission_remains_unknown_across_restart(tmp_path):
    started = asyncio.Event()

    class SlowClient(FakeClient):
        async def submit(self, slug, values, budget):
            self.posts += 1
            started.set()
            await asyncio.sleep(60)

    client = SlowClient()
    job = asyncio.create_task(Engine(client, Store(tmp_path), FakeResults()).generate(**controls()))
    await started.wait()
    job.cancel()
    with pytest.raises(asyncio.CancelledError):
        await job
    with pytest.raises(ModellixError, match="unknown"):
        await Engine(client, Store(tmp_path), FakeResults()).generate(**controls())
    assert client.posts == 1


@pytest.mark.parametrize("unknown", [False, True])
async def test_reviewing_a_changed_schema_does_not_replay_the_same_attempt(tmp_path, unknown):
    class ChangingSchema(FakeClient):
        document = copy.deepcopy(DOCUMENT)

        async def schema(self, slug, budget):
            return self.document

    client, store = ChangingSchema(), Store(tmp_path)
    client.fail = unknown
    engine = Engine(client, store, FakeResults())
    if unknown:
        with pytest.raises(ModellixError):
            await engine.generate(**controls(mode="Submit only"))
    else:
        await engine.generate(**controls(mode="Submit only"))
    client.document = copy.deepcopy(DOCUMENT)
    client.document["description"] = "Upstream documentation changed after submission"
    reviewed_hash = ModelSchema(client.document, "vendor/model.2").hash
    client.fail = False
    if unknown:
        with pytest.raises(ModellixError, match="unknown"):
            await engine.generate(**controls(schema_hash=reviewed_hash))
    else:
        result = await engine.generate(**controls(schema_hash=reviewed_hash))
        assert result["task_id"] == "task-1"
    assert client.posts == 1
    await engine.generate(**controls(schema_hash=reviewed_hash, run_nonce="explicit-new-attempt"))
    assert client.posts == 2


@pytest.mark.parametrize("damaged", [None, {}, {"state": "ready"},
                                    {"state": "submitted", "task_id": False},
                                    {"state": "unrecognized"}])
async def test_structurally_damaged_attempt_does_not_create_a_second_task(tmp_path, damaged):
    client, store = FakeClient(), Store(tmp_path)
    await Engine(client, store, FakeResults()).generate(**controls(mode="Submit only"))
    with store.connect() as db:
        db.execute("UPDATE records SET value=? WHERE kind='attempt'", (json.dumps(damaged),))
    with pytest.raises(ModellixError, match="damaged"):
        await Engine(client, Store(tmp_path), FakeResults()).generate(**controls())
    assert client.posts == 1


async def test_generation_deadline_includes_catalog_loading(tmp_path):
    class SlowCatalog(FakeClient):
        async def models(self, budget):
            await budget.sleep(0.1)
            return await super().models(budget)

    client = SlowCatalog()
    with pytest.raises(ModellixError, match="deadline"):
        await Engine(client, Store(tmp_path), FakeResults()).generate(**controls(timeout=0.02))
    assert client.posts == 0


async def test_generation_deadline_includes_waiting_for_an_attempt_lock(tmp_path):
    from filelock import FileLock

    from modellix.cache import digest

    client, store = FakeClient(), Store(tmp_path)
    await Engine(client, store, FakeResults()).generate(**controls(mode="Submit only"))
    key = store.records("attempt")[0][0]
    with FileLock(str(store.root / (digest("attempt:" + key) + ".lock"))):
        with pytest.raises(ModellixError, match="deadline"):
            await asyncio.wait_for(Engine(client, store, FakeResults()).generate(
                **controls(timeout=0.02)), 0.3)
    assert client.posts == 1


async def test_submission_error_exposes_confirmed_id_and_recovers_without_reposting(tmp_path):
    class AcceptedError(FakeClient):
        events = []

        async def submit(self, slug, values, budget):
            self.posts += 1
            raise ModellixError("Response failed after acceptance", task_id="task-1")

        def progress(self, **event):
            self.events.append(event)

    client, store = AcceptedError(), Store(tmp_path)
    with pytest.raises(ModellixError):
        await Engine(client, store, FakeResults()).generate(**controls())
    assert any(e.get("task_id") == "task-1" for e in client.events)
    result = await Engine(client, store, FakeResults()).generate(**controls())
    assert result["task_id"] == "task-1" and client.posts == 1
