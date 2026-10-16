from types import SimpleNamespace

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from modellix import routes


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
