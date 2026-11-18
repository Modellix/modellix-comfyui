from types import SimpleNamespace

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from modellix import routes


@pytest.mark.parametrize(("host", "peer"), [
    ("localhost:8000", "127.0.0.1"),
    ("127.0.0.1:8000", "127.0.0.1"),
    ("[::1]:8000", "::1"),
])
def test_local_credential_routes_accept_loopback_hosts(monkeypatch, host, peer):
    monkeypatch.setattr(routes, "_nonce", "fixture-csrf")
    request = SimpleNamespace(
        remote=peer, host=host, scheme="http", content_type="application/json",
        headers={"Origin": f"http://{host}", "Sec-Fetch-Site": "same-origin",
                 "X-Modellix-CSRF": "fixture-csrf"},
    )
    routes.local_request(request)
    routes.local_request(request, write=True)

    for attribute, value in (("remote", "192.0.2.1"), ("content_type", "text/plain")):
        with monkeypatch.context() as patch:
            patch.setattr(request, attribute, value)
            with pytest.raises(web.HTTPForbidden):
                routes.local_request(request, write=True)

    for header, value in (("Origin", "https://untrusted.example"),
                          ("Sec-Fetch-Site", "cross-site"),
                          ("X-Modellix-CSRF", "invalid")):
        with monkeypatch.context() as patch:
            patch.setitem(request.headers, header, value)
            with pytest.raises(web.HTTPForbidden):
                routes.local_request(request, write=True)


async def test_custom_result_directory_is_served_without_exposing_state(monkeypatch, tmp_path):
    root = tmp_path / "custom-results"
    root.mkdir()
    name = "a" * 32 + ".png"
    (root / name).write_bytes(b"cached-result")
    (tmp_path / "state.sqlite3").write_bytes(b"private-state")
    monkeypatch.setattr(routes.runtime, "results", lambda: SimpleNamespace(root=root))
    app = web.Application()
    app.router.add_get("/modellix/results/{filename}", routes.result_file)
    async with TestClient(TestServer(app, host="localhost")) as client:
        async with client.get("/modellix/results/" + name) as response:
            assert response.status == 200
            assert await response.read() == b"cached-result"
            assert response.headers["X-Content-Type-Options"] == "nosniff"
        for filename in ("state.sqlite3", "AGENTS.md", "b" * 32 + ".png", "../state.sqlite3"):
            async with client.get("/modellix/results/" + filename) as response:
                assert response.status == 404
        for headers in ({"Origin": "https://untrusted.example"}, {"Host": "untrusted.example"}):
            async with client.get("/modellix/results/" + name, headers=headers) as response:
                assert response.status == 403
                assert (await response.json())["error"].startswith("Local same-origin request required.")
