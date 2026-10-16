import json

import pytest

from modellix.auth import Auth
from modellix.cache import Store
from modellix.client import Deadline
from modellix.errors import ModellixError
from modellix.media import Blob, upload_blob


class SecureBackend:
    value = None

    def get_password(self, *args):
        return self.value

    def set_password(self, service, user, value):
        self.value = value

    def delete_password(self, *args):
        self.value = None


def test_credential_priority_clear_and_secure_restart(monkeypatch, tmp_path):
    backend = SecureBackend()
    monkeypatch.setattr(Auth, "backend", lambda self: backend)
    monkeypatch.setenv("MODELLIX_API_KEY", "fake-env")
    auth = Auth()
    assert auth.resolve() == ("fake-env", "environment")
    auth.apply("save", "fake-secure")
    auth.apply("session", "fake-session")
    assert auth.resolve() == ("fake-session", "session")
    assert Auth().resolve() == ("fake-secure", "secure storage")
    store = Store(tmp_path)
    first = auth.snapshot(store)
    assert repr(first) == object.__repr__(first) or "fake-session" not in repr(first)
    assert first.context == AuthContext(auth, store)
    assert "fake-session" not in json.dumps(auth.status())
    auth.apply("clear")
    assert not auth.status()["configured"]
    assert backend.value is None
    with pytest.raises(ModellixError):
        auth.resolve()
    auth.apply("environment")
    assert auth.resolve() == ("fake-env", "environment")


def AuthContext(auth, store):
    return auth.snapshot(store).context


def test_insecure_backend_is_rejected(monkeypatch):
    import keyring
    monkeypatch.setattr(keyring, "get_keyring", lambda: object())
    with pytest.raises(ModellixError, match="plaintext"):
        Auth().backend()


async def test_unknown_upload_is_not_replayed(tmp_path):
    class Client:
        class credential:
            context = "fake"
        calls = 0

        async def upload(self, blob, budget):
            self.calls += 1
            raise ModellixError("lost response", unknown=True)

    store, client = Store(tmp_path), Client()
    blob = Blob(b"test", "image/png", "input.png")
    for _ in range(2):
        with pytest.raises(ModellixError):
            await upload_blob(blob, client, store, Deadline(2))
    assert client.calls == 1
    with pytest.raises(ModellixError):
        await upload_blob(blob, client, store, Deadline(2), nonce="explicit-new-upload")
    assert client.calls == 2


def test_corrupt_attempt_protection_is_not_treated_as_empty(tmp_path):
    store = Store(tmp_path)
    with store.connect() as db:
        db.execute("INSERT INTO records VALUES ('attempt','test','invalid',0)")
    with pytest.raises(ModellixError, match="damaged"):
        store.get("attempt", "test")


def test_catalog_cache_expiry_and_identity_isolation(tmp_path):
    store = Store(tmp_path)
    assert store.identity("fake-a") != store.identity("fake-b")
    store.put("catalog", "profile-a", ["a"], ttl=-1)
    assert store.get("catalog", "profile-a") is None
    assert store.get("catalog", "profile-a", stale=True) == ["a"]
