import json
import time
from types import SimpleNamespace

import pytest
from filelock import FileLock

from modellix.cache import Store
from modellix.client import Deadline
from modellix.errors import ModellixError
from modellix.media import Results, pin


def test_cache_cleanup_preserves_live_results_and_attempt_records(tmp_path):
    store = Store(tmp_path / "state")
    results = Results(tmp_path / "results", store)
    stale = results.root / "stale.png"
    active = results.root / "active.png"
    foreign = results.root / "foreign.png"
    for path in (stale, active, foreign):
        path.write_bytes(b"content")
        store.put("result", path.name, {"path": str(path), "created": time.time() - 8 * 86400})
    store.put("attempt", "protected", {"state": "outcome_unknown"})
    pin(active)
    with FileLock(str(foreign) + ".lease-other-process"):
        results.clean()
        assert foreign.exists()
    assert active.exists() and not stale.exists()
    assert store.get("attempt", "protected")["state"] == "outcome_unknown"


def test_abandoned_parts_are_reclaimed_but_live_parts_remain(tmp_path):
    store = Store(tmp_path / "state")
    results = Results(tmp_path / "results", store)
    dead = results.root / "dead.part"
    live = results.root / "live.part"
    dead.write_bytes(b"partial")
    live.write_bytes(b"partial")
    with FileLock(str(live) + ".lock"):
        results.clean_parts()
        assert live.exists()
    assert not dead.exists()


def test_state_contains_no_request_body_or_credential(tmp_path):
    store = Store(tmp_path)
    context = store.identity("fake-credential-never-persist")
    store.put("attempt", "test", {"state": "submitted", "task_id": "test-task", "context": context})
    with store.connect() as db:
        dump = "\n".join(db.iterdump())
    assert "fake-credential-never-persist" not in dump
    assert "prompt" not in json.dumps(store.get("attempt", "test"))
    opaque = store.public_fingerprint(context)
    assert opaque != context
    assert opaque == Store(tmp_path).public_fingerprint(context)


async def test_undecodable_download_is_fetched_again_when_the_task_is_recovered(monkeypatch, tmp_path):
    class Download:
        credential = SimpleNamespace(context="test-profile")
        downloads = 0

        async def download(self, url, path, budget, **kwargs):
            self.downloads += 1
            content = b"broken-image" if self.downloads == 1 else b"valid-image"
            path.write_bytes(content)
            return "image/png", len(content)

    def decode(path, kind):
        if path.read_bytes() == b"broken-image":
            raise ValueError("truncated image")
        return "decoded-native-test-sentinel"

    monkeypatch.setattr("modellix.media.decode", decode)
    client, store = Download(), Store(tmp_path / "state")
    results = Results(tmp_path / "results", store)
    task = {"task_id": "existing-task", "result": {"resources": [
        {"type": "image", "url": "https://public.example/result.png"}]}}
    with pytest.raises(ModellixError, match="decoding"):
        await results.materialize(client, task, Deadline(2))
    outputs, _ = await results.materialize(client, task, Deadline(2))
    assert outputs["image"] == ["decoded-native-test-sentinel"]
    assert client.downloads == 2
