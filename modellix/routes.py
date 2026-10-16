"""Loopback-only, same-origin, write-only configuration routes."""
import hmac
import ipaddress
import secrets
from urllib.parse import urlsplit

from aiohttp import web

from . import runtime
from .auth import auth
from .catalog import Catalog
from .client import Client
from .errors import ModellixError

_registered = False
_nonce = secrets.token_urlsafe(32)


def local_request(request, write=False):
    try:
        remote = ipaddress.ip_address(request.remote)
        host = urlsplit("http://" + request.host)
        if not remote.is_loopback or host.hostname != "localhost":
            raise ValueError
        origin = request.headers.get("Origin")
        if origin and origin != f"{request.scheme}://{request.host}":
            raise ValueError
        if request.headers.get("Sec-Fetch-Site") == "cross-site":
            raise ValueError
        if write and (request.content_type != "application/json" or
            not hmac.compare_digest(request.headers.get("X-Modellix-CSRF", ""), _nonce)):
            raise ValueError
    except (ValueError, TypeError):
        raise web.HTTPForbidden(text="Local same-origin request required.") from None


def register():
    global _registered
    if _registered:
        return
    from server import PromptServer
    routes = PromptServer.instance.routes

    @routes.get("/modellix/session")
    async def status(request):
        local_request(request)
        return web.json_response({**auth.status(), "csrf": _nonce},
                                 headers={"Cache-Control": "no-store"})

    @routes.post("/modellix/credentials")
    async def credentials(request):
        local_request(request, True)
        if request.content_length and request.content_length > 16384:
            raise web.HTTPRequestEntityTooLarge(max_size=16384, actual_size=request.content_length)
        try:
            body = await request.json()
            result = auth.apply(body.get("action"), body.get("key", ""))
            return web.json_response(result, headers={"Cache-Control": "no-store"})
        except ModellixError as error:
            return web.json_response({"error": str(error)}, status=400)
        except (ValueError, TypeError, AttributeError):
            return web.json_response({"error": "Invalid configuration request."}, status=400)

    @routes.get("/modellix/models")
    async def models(request):
        local_request(request)
        try:
            async with Client(auth.snapshot(runtime.store())) as client:
                result = await Catalog(runtime.store(), client).models(
                    refresh=request.query.get("refresh") == "true",
                    offline=request.query.get("offline") == "true")
            return web.json_response({"models": result}, headers={"Cache-Control": "no-store"})
        except ModellixError as error:
            return web.json_response({"error": str(error)}, status=400)

    @routes.get("/modellix/schema")
    async def schema(request):
        local_request(request)
        try:
            async with Client(auth.snapshot(runtime.store())) as client:
                model = await Catalog(runtime.store(), client).schema(
                    request.query.get("slug", ""), refresh=request.query.get("refresh") == "true",
                    offline=request.query.get("offline") == "true")
            return web.json_response({"schema": model.schema, "hash": model.hash},
                                     headers={"Cache-Control": "no-store"})
        except ModellixError as error:
            return web.json_response({"error": str(error)}, status=400)

    @routes.post("/modellix/cache/clean")
    async def clean(request):
        local_request(request, True)
        runtime.results().clean()
        return web.json_response({"status": "Unused expired results cleaned. Active files and "
                                            "paid-attempt records were preserved."})

    _registered = True
