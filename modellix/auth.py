"""Write-only local credentials; no plaintext keyring fallback."""
import os
from dataclasses import dataclass
from threading import RLock

from .errors import ModellixError

SERVICE = "modellix-comfyui:https://api.modellix.ai"


@dataclass(frozen=True, repr=False)
class Credential:
    key: str
    context: str


class Auth:
    def __init__(self):
        self.session = None
        self.cleared = False
        self.revision = 0
        self.lock = RLock()

    def backend(self):
        import keyring
        backend = keyring.get_keyring()
        allowed = ("keyring.backends.Windows", "keyring.backends.macOS",
                   "keyring.backends.SecretService", "keyring.backends.kwallet")
        if not type(backend).__module__.startswith(allowed):
            raise ModellixError("OS secure storage is unavailable. Use Session Only or "
                                "MODELLIX_API_KEY; plaintext storage is disabled.")
        return backend

    def resolve(self):
        with self.lock:
            if self.cleared:
                raise ModellixError("Key cleared. Explicitly apply a key or enable Environment.")
            if self.session:
                return self.session, "session"
            try:
                value = self.backend().get_password(SERVICE, "default")
            except Exception:
                value = None
            if value:
                return value, "secure storage"
            value = os.environ.get("MODELLIX_API_KEY")
            if value:
                return value, "environment"
            raise ModellixError("Configure your API key in Settings > Modellix.")

    def snapshot(self, store):
        key, _ = self.resolve()
        return Credential(key, store.identity(key))

    def status(self):
        try:
            _, source = self.resolve()
            configured = True
        except ModellixError:
            source, configured = "cleared" if self.cleared else "none", False
        return {"configured": configured, "source": source, "revision": self.revision}

    def apply(self, action, key=""):
        with self.lock:
            if action in ("session", "save"):
                if not isinstance(key, str) or not key.strip() or any(c.isspace() for c in key):
                    raise ModellixError("Enter a valid API key without whitespace.")
                if action == "save":
                    try:
                        self.backend().set_password(SERVICE, "default", key)
                    except ModellixError:
                        raise
                    except Exception:
                        raise ModellixError("Secure storage write failed; key was not applied.") from None
                    self.session = None
                else:
                    self.session = key
                self.cleared = False
            elif action == "clear":
                self.session, self.cleared = None, True
                try:
                    backend = self.backend()
                    if backend.get_password(SERVICE, "default"):
                        backend.delete_password(SERVICE, "default")
                except Exception:
                    self.revision += 1
                    raise ModellixError("Session cleared and environment disabled. Secure storage "
                                        "could not be cleared; unlock it and clear again.") from None
            elif action == "environment":
                self.session, self.cleared = None, False
            else:
                raise ModellixError("Unknown credential action.")
            self.revision += 1
        return self.status()


auth = Auth()
