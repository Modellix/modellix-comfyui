import asyncio
from contextlib import asynccontextmanager

import pytest
from aiohttp import web

from modellix.auth import Credential
from modellix.client import Client, Deadline, resources, task_id
from modellix.errors import ModellixError, safe_text


@asynccontextmanager
async def api(handler):
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "localhost", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    try:
        yield f"http://localhost:{port}"
    finally:
        await runner.cleanup()


@pytest.mark.parametrize("status", [400, 401, 402, 404, 413, 429, 500, 503, 200])
async def test_paid_post_is_never_retried(status):
    requests = []

    async def handle(request):
        requests.append(request.method)
        return web.json_response({"code": 99, "message": "failure"}, status=status)

    async with api(handle) as origin, Client(Credential("fake-test-key", "fake"), api=origin) as client:
        with pytest.raises(ModellixError):
            await client.submit("vendor/model.2", {"prompt": "test"}, Deadline(2))
    assert requests == ["POST"]


async def test_public_schema_has_no_auth_and_slug_dot_is_preserved():
    requests = []

    async def handle(request):
        requests.append((request.path, request.headers.get("Authorization")))
        return web.json_response({"post": {}})

    async with api(handle) as origin, Client(Credential("fake", "context"), web=origin) as client:
        await client.schema("vendor/model.2-lite", Deadline(2))
    assert requests == [("/models/vendor/model.2-lite/api_schema", None)]


async def test_redirect_never_sends_credentials_to_redirect_target():
    count = 0

    async def handle(request):
        nonlocal count
        count += 1
        return web.Response(status=302, headers={"Location": "/other"})

    async with api(handle) as origin, Client(Credential("fake", "context"), api=origin) as client:
        with pytest.raises(ModellixError):
            await client.models(Deadline(2))
    assert count == 1


async def test_error_with_task_id_is_retained():
    async def handle(request):
        return web.json_response({"data": {"task_id": "task-1"}}, status=500)

    async with api(handle) as origin, Client(Credential("fake", "c"), api=origin) as client:
        with pytest.raises(ModellixError) as error:
            await client.submit("vendor/model", {}, Deadline(2))
    assert error.value.task_id == "task-1"


async def test_safe_get_retries_share_one_budget():
    count, sleeps = 0, []

    class Budget(Deadline):
        async def sleep(self, seconds):
            sleeps.append(seconds)
            self.remaining()

    async def handle(request):
        nonlocal count
        count += 1
        if count < 3:
            return web.Response(status=503, headers={"Retry-After": "1"})
        return web.json_response({"models": []})

    async with api(handle) as origin, Client(Credential("fake", "c"), api=origin) as client:
        assert await client.models(Budget(2)) == []
    assert count == 3 and sleeps == [1, 1]


async def test_chunked_schema_is_read_to_completion():
    async def handle(request):
        response = web.StreamResponse(headers={"Content-Type": "application/json"})
        await response.prepare(request)
        await response.write(b'{"post":')
        await asyncio.sleep(0.01)
        await response.write(b'{"description":"complete"}}')
        await response.write_eof()
        return response

    async with api(handle) as origin, Client(Credential("fake", "c"), web=origin) as client:
        assert (await client.schema("v/m", Deadline(2)))["post"]["description"] == "complete"


@pytest.mark.parametrize("url", ["file:///secret", "http://public.test/a", "https://localhost/a",
                                 "https://10.0.0.1/a", "https://[::1]/a"])
async def test_resource_download_rejects_private_or_non_https(url, tmp_path):
    async with Client(Credential("fake", "c")) as client:
        with pytest.raises(ModellixError):
            await client.download(url, tmp_path / "output", Deadline(2))
    assert not list(tmp_path.glob("*.part"))


async def test_cancel_closes_in_flight_request():
    started = asyncio.Event()
    release = asyncio.Event()

    async def handle(request):
        started.set()
        await release.wait()
        return web.json_response({})

    async with api(handle) as origin, Client(Credential("fake", "c"), api=origin) as client:
        job = asyncio.create_task(client.query("task-1", Deadline(10), wait=True))
        await started.wait()
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(job, 1)
        release.set()


@pytest.mark.parametrize("value", ["../bad", "", "a/b", "bad secret", None])
def test_task_id_is_safe(value):
    with pytest.raises(ModellixError):
        task_id(value)


def test_safe_error_text():
    text = safe_text("Bearer fake-secret https://cdn.test/file?signature=secret sk-test123", "fake-secret")
    assert "fake-secret" not in text and "signature" not in text and "sk-test123" not in text


@pytest.mark.parametrize("body", ["not-json", '{"code":0,"data":{}}', '{"code":77}', '[]'])
async def test_invalid_post_response_never_replays(body):
    count = 0

    async def handle(request):
        nonlocal count
        count += 1
        return web.Response(text=body)

    async with api(handle) as origin, Client(Credential("fake", "c"), api=origin) as client:
        with pytest.raises(ModellixError):
            await client.submit("v/m", {}, Deadline(2))
    assert count == 1


@pytest.mark.parametrize("status", ["pending", "processing", "success", "failed", "canceled", "invented"])
async def test_actual_task_status_protocol(status):
    async def handle(request):
        return web.json_response({"code": 0, "data": {"task_id": "test", "status": status}})

    async with api(handle) as origin, Client(Credential("fake", "c"), api=origin) as client:
        if status == "invented":
            with pytest.raises(ModellixError, match="Unknown"):
                await client.query("test", Deadline(2))
        else:
            assert (await client.query("test", Deadline(2)))["status"] == status


@pytest.mark.parametrize("value", [{}, {"resources": []}, {"resources": "invalid"}])
def test_success_without_media_is_an_error(value):
    with pytest.raises(ModellixError):
        resources({"task_id": "test", "result": value})


@pytest.mark.parametrize("data", [None, [], "invalid"])
async def test_malformed_task_data_retains_recoverable_id(data):
    async def handle(request):
        return web.json_response({"code": 0, "data": data})

    async with api(handle) as origin, Client(Credential("fake", "c"), api=origin) as client:
        with pytest.raises(ModellixError) as error:
            await client.query("test-task", Deadline(2))
        assert error.value.task_id == "test-task"


async def test_400_retains_field_guidance_but_not_credentials_or_request_content():
    requests = []

    async def handle(request):
        requests.append(request.method)
        return web.json_response({"message": 'QA /prompt sentinel field. Bearer fake-key '
            'api_key="another-secret" https://cdn.test/a?signature=private '
            '{"prompt":"private prompt","voice":"private voice"}'}, status=400)

    async with api(handle) as origin, Client(Credential("fake-key", "c"), api=origin) as client:
        with pytest.raises(ModellixError) as error:
            await client.submit("qa/field-model", {"prompt": "private prompt", "nested": {
                "voice": "private voice"}}, Deadline(2))
    message = str(error.value)
    assert "QA /prompt sentinel field" in message and "qa/field-model" in message
    for private in ("fake-key", "another-secret", "signature=private", "private prompt", "private voice"):
        assert private not in message
    assert error.value.unknown and requests == ["POST"]


@pytest.mark.parametrize("operation", ["task", "schema", "generate"])
async def test_404_uses_requested_identity_without_response_metadata(operation):
    async def handle(request):
        return web.json_response({"code": 404}, status=404)

    async with api(handle) as origin, Client(Credential("fake", "c"), api=origin, web=origin) as client:
        with pytest.raises(ModellixError) as error:
            if operation == "task":
                await client.query("missing-task", Deadline(2))
            elif operation == "schema":
                await client.schema("qa/missing-model", Deadline(2))
            else:
                await client.submit("qa/missing-model", {}, Deadline(2))
    expected = "missing-task" if operation == "task" else "qa/missing-model"
    assert expected in str(error.value)
    assert error.value.status == 404


async def test_task_error_response_cannot_replace_requested_task_identity():
    async def handle(request):
        return web.json_response({"data": {"task_id": "wrong-task"}}, status=404)

    async with api(handle) as origin, Client(Credential("fake", "c"), api=origin) as client:
        with pytest.raises(ModellixError) as error:
            await client.query("requested-task", Deadline(2))
    assert error.value.task_id == "requested-task" and "wrong-task" not in str(error.value)


async def test_retry_deadline_retains_requested_task_identity():
    class ExpiredBudget(Deadline):
        async def sleep(self, seconds):
            raise ModellixError("Local deadline expired")

    async def handle(request):
        return web.Response(status=503)

    async with api(handle) as origin, Client(Credential("fake", "c"), api=origin) as client:
        with pytest.raises(ModellixError) as error:
            await client.query("current-deadline-task", ExpiredBudget(2))
    assert error.value.task_id == "current-deadline-task"


@pytest.mark.parametrize("result", ["invalid", ["resource"]])
def test_malformed_success_result_is_a_recoverable_error(result):
    with pytest.raises(ModellixError) as error:
        resources({"task_id": "test-task", "result": result})
    assert error.value.task_id == "test-task"
