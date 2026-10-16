import json
import time

from filelock import FileLock

from modellix.cache import Store
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
