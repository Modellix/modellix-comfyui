"""Per-credential catalog and shared public schema cache."""
from .client import Deadline
from .errors import ModellixError
from .schema import MEDIA_TYPES, ModelSchema


class Catalog:
    def __init__(self, store, client):
        self.store, self.client = store, client

    async def models(self, *, refresh=False, offline=False, budget=None):
        key = self.client.credential.context
        cached = self.store.get("catalog", key, stale=offline)
        if cached and not refresh:
            return cached
        if offline:
            raise ModellixError("No offline catalog. Configure a key and refresh online.")
        models = await self.client.models(budget or Deadline(60))
        result = []
        for model in models:
            item = {k: model[k] for k in ("slug", "type", "name", "description", "price",
                    "series_name", "docs_url") if k in model}
            item["supported"] = item.get("type") in MEDIA_TYPES
            item["reason"] = "" if item["supported"] else "Text/unknown output is outside IMAGE/VIDEO/AUDIO."
            result.append(item)
        self.store.put("catalog", key, result, 600)
        return result

    async def schema(self, slug, *, refresh=False, offline=False, budget=None):
        cached = self.store.get("schema", slug, stale=offline)
        if cached and not refresh:
            return ModelSchema(cached, slug)
        if offline:
            raise ModellixError("No cached schema for offline editing.")
        document = await self.client.schema(slug, budget or Deadline(60))
        schema = ModelSchema(document, slug)
        self.store.put("schema", slug, document, 3600)
        return schema
