import asyncio
import gzip
import socket
from typing import Dict, Optional, Sequence

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.models.requests import ApiRequest, HttpMethod
from app.services.request_execution import ApiRequestService

PUBLIC_IP = "93.184.216.34"
TARGET_URL = "https://target.test/api"


def make_settings(**overrides: object) -> Settings:
    return Settings(outbound_allowed_hosts="target.test", **overrides)


async def public_resolver(host: str, port: int) -> Sequence[str]:
    del host, port
    return (PUBLIC_IP,)


def run_service(
    request: ApiRequest,
    handler,
    *,
    settings: Optional[Settings] = None,
    resolver=public_resolver,
):
    async def execute():
        service = ApiRequestService(
            settings or make_settings(),
            transport=httpx.MockTransport(handler),
            resolver=resolver,
        )
        try:
            return await service.execute(request)
        finally:
            await service.aclose()

    return asyncio.run(execute())


@pytest.mark.parametrize(
    "method",
    [
        HttpMethod.GET,
        HttpMethod.POST,
        HttpMethod.PUT,
        HttpMethod.PATCH,
        HttpMethod.DELETE,
        HttpMethod.HEAD,
        HttpMethod.OPTIONS,
    ],
)
def test_executes_each_supported_http_method(method: HttpMethod) -> None:
    observed_methods = []

    async def handler(request: httpx.Request) -> httpx.Response:
        observed_methods.append(request.method)
        return httpx.Response(204, request=request)

    response = run_service(ApiRequest(url=TARGET_URL, method=method), handler)

    assert response.status_code == 204
    assert observed_methods == [method.value]


def test_success_captures_response_and_parses_json() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "application/json; charset=utf-8"},
            json={"result": "ok"},
        )

    response = run_service(ApiRequest(url=TARGET_URL), handler)

    assert response.status_code == 200
    assert response.body == {"result": "ok"}
    assert response.is_json is True
    assert response.content_type == "application/json; charset=utf-8"
    assert response.headers["content-type"] == "application/json; charset=utf-8"
    assert response.execution_time_ms is not None
    assert response.execution_time_ms >= 0
    assert response.error is None


def test_sends_query_parameters_headers_and_json_body() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {"existing": "1", "page": "2"}
        assert request.headers["x-client"] == "edgetest"
        assert request.headers["content-type"] == "application/json"
        assert request.content == b'{"active":true}'
        return httpx.Response(200, request=request, json={"accepted": True})

    request = ApiRequest(
        url="https://target.test/api?existing=1",
        method="POST",
        headers={"x-client": "edgetest"},
        query_params={"page": "2"},
        body={"active": True},
    )

    response = run_service(request, handler)

    assert response.status_code == 200
    assert response.body == {"accepted": True}


@pytest.mark.parametrize(
    ("headers", "body", "expected_body", "expected_json"),
    [
        ({"content-type": "text/plain"}, b"hello", "hello", False),
        ({"content-type": "application/octet-stream"}, b"\xff\x00", "\ufffd\x00", False),
        ({"content-type": "text/plain"}, b'{"valid":true}', {"valid": True}, True),
        ({"content-type": "text/plain"}, b"", None, False),
    ],
)
def test_handles_plain_text_empty_and_non_json_responses(
    headers: Dict[str, str],
    body: bytes,
    expected_body: object,
    expected_json: bool,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, headers=headers, content=body)

    response = run_service(ApiRequest(url=TARGET_URL), handler)

    assert response.body == expected_body
    assert response.is_json is expected_json


@pytest.mark.parametrize("status_code", [400, 404, 500, 503])
def test_http_error_statuses_are_responses_not_transport_errors(
    status_code: int,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status_code,
            request=request,
            headers={"content-type": "text/plain"},
            text="upstream error",
        )

    response = run_service(ApiRequest(url=TARGET_URL), handler)

    assert response.status_code == status_code
    assert response.body == "upstream error"
    assert response.error is None


def test_connection_error_is_sanitized_and_distinguished() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret connection detail", request=request)

    response = run_service(ApiRequest(url=TARGET_URL), handler)

    assert response.status_code is None
    assert response.error is not None
    assert response.error.code == "connection_error"
    assert "secret connection detail" not in response.error.message


def test_transport_timeout_is_reported_as_timeout() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("sensitive timeout detail", request=request)

    response = run_service(ApiRequest(url=TARGET_URL), handler)

    assert response.error is not None
    assert response.error.code == "timeout"
    assert "sensitive" not in response.error.message


def test_total_execution_timeout_covers_slow_transport() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.3)
        return httpx.Response(200, request=request)

    response = run_service(
        ApiRequest(url=TARGET_URL, timeout=0.1),
        handler,
    )

    assert response.error is not None
    assert response.error.code == "timeout"


def test_dns_resolution_error_is_distinguished() -> None:
    async def failed_resolver(host: str, port: int) -> Sequence[str]:
        del host, port
        raise socket.gaierror("sensitive DNS failure")

    async def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("HTTP client must not run when DNS resolution fails")

    response = run_service(
        ApiRequest(url=TARGET_URL),
        handler,
        resolver=failed_resolver,
    )

    assert response.error is not None
    assert response.error.code == "dns_resolution_failed"


def test_unlisted_host_is_blocked_before_resolution() -> None:
    async def unexpected_resolver(host: str, port: int) -> Sequence[str]:
        pytest.fail("unlisted host must be rejected before DNS resolution")

    async def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("unlisted host must not reach the HTTP transport")

    response = run_service(
        ApiRequest(url="https://unlisted.test"),
        handler,
        resolver=unexpected_resolver,
    )

    assert response.error is not None
    assert response.error.code == "destination_not_allowed"


@pytest.mark.parametrize(
    "addresses",
    [
        ("10.0.0.5",),
        ("169.254.169.254",),
        ("93.184.216.34", "127.0.0.1"),
    ],
)
def test_private_metadata_and_mixed_dns_answers_are_blocked(
    addresses: Sequence[str],
) -> None:
    async def resolver(host: str, port: int) -> Sequence[str]:
        del host, port
        return addresses

    async def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("unsafe DNS results must not reach the HTTP transport")

    response = run_service(
        ApiRequest(url=TARGET_URL),
        handler,
        resolver=resolver,
    )

    assert response.error is not None
    assert response.error.code == "destination_not_allowed"


def test_cloud_metadata_host_is_blocked() -> None:
    async def unexpected_resolver(host: str, port: int) -> Sequence[str]:
        pytest.fail("metadata host must be rejected before DNS resolution")

    async def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("metadata host must not reach the HTTP transport")

    response = run_service(
        ApiRequest(url="http://metadata.google.internal"),
        handler,
        settings=Settings(),
        resolver=unexpected_resolver,
    )

    assert response.error is not None
    assert response.error.code == "destination_not_allowed"


def test_authorized_localhost_is_allowed_but_only_to_loopback() -> None:
    async def local_resolver(host: str, port: int) -> Sequence[str]:
        del host, port
        return ("127.0.0.1",)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, text="local")

    response = run_service(
        ApiRequest(url="http://localhost:8765/health"),
        handler,
        settings=Settings(allow_localhost_targets=True),
        resolver=local_resolver,
    )

    assert response.status_code == 200
    assert response.body == "local"


def test_authorized_localhost_rejects_non_loopback_dns_results() -> None:
    async def unsafe_resolver(host: str, port: int) -> Sequence[str]:
        del host, port
        return (PUBLIC_IP,)

    async def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("localhost with public DNS results must be blocked")

    response = run_service(
        ApiRequest(url="http://localhost:8765/health"),
        handler,
        settings=Settings(allow_localhost_targets=True),
        resolver=unsafe_resolver,
    )

    assert response.error is not None
    assert response.error.code == "destination_not_allowed"


def test_localhost_is_denied_by_default() -> None:
    async def resolver(host: str, port: int) -> Sequence[str]:
        del host, port
        return ("127.0.0.1",)

    async def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("localhost is disabled by default")

    response = run_service(
        ApiRequest(url="http://127.0.0.1:8765/health"),
        handler,
        resolver=resolver,
    )

    assert response.error is not None
    assert response.error.code == "destination_not_allowed"


def test_only_configured_local_demo_ports_are_allowed() -> None:
    async def resolver(host: str, port: int) -> Sequence[str]:
        del host, port
        return ("127.0.0.1",)

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, text="demo")

    allowed = run_service(
        ApiRequest(url="http://127.0.0.1:8001/health"),
        handler,
        settings=Settings(),
        resolver=resolver,
    )
    blocked = run_service(
        ApiRequest(url="http://127.0.0.1:8002/health"),
        handler,
        settings=Settings(),
        resolver=resolver,
    )

    assert allowed.status_code == 200
    assert blocked.error is not None
    assert blocked.error.code == "destination_not_allowed"


def test_redirect_is_returned_without_following_it() -> None:
    requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            302,
            request=request,
            headers={"location": "http://169.254.169.254/latest/meta-data"},
        )

    response = run_service(ApiRequest(url=TARGET_URL), handler)

    assert response.status_code == 302
    assert len(requests) == 1


class ChunkStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b"x" * 600
        yield b"y" * 500

    async def aclose(self) -> None:
        return None


def test_streaming_response_size_is_limited() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            headers={"content-type": "text/plain"},
            stream=ChunkStream(),
        )

    response = run_service(
        ApiRequest(url=TARGET_URL),
        handler,
        settings=make_settings(max_response_bytes=1_024),
    )

    assert response.status_code == 200
    assert response.body is None
    assert response.error is not None
    assert response.error.code == "response_too_large"


def test_compressed_response_is_rejected_without_decompression() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["accept-encoding"] == "identity"
        return httpx.Response(
            200,
            request=request,
            headers={"content-encoding": "gzip", "content-type": "text/plain"},
            content=gzip.compress(b"compressed-data"),
        )

    response = run_service(ApiRequest(url=TARGET_URL), handler)

    assert response.status_code == 200
    assert response.error is not None
    assert response.error.code == "unsupported_content_encoding"


def test_versioned_route_executes_against_controlled_local_asgi_app(tmp_path) -> None:
    upstream = FastAPI()

    @upstream.post("/echo")
    async def echo(request: Request) -> Dict[str, object]:
        return {
            "method": request.method,
            "query": request.query_params.get("page"),
            "header": request.headers.get("x-test"),
            "body": await request.json(),
        }

    async def resolver(host: str, port: int) -> Sequence[str]:
        del host, port
        return (PUBLIC_IP,)

    application = create_app(
        settings=make_settings(database_path=str(tmp_path / "request-route.sqlite3")),
        transport=httpx.ASGITransport(app=upstream),
        resolver=resolver,
    )

    with TestClient(application) as test_client:
        response = test_client.post(
            "/api/v1/requests/execute",
            json={
                "url": "https://target.test/echo",
                "method": "POST",
                "headers": {"x-test": "present"},
                "query_params": {"page": "3"},
                "body": {"value": 7},
            },
        )

    assert response.status_code == 200
    assert response.json()["body"] == {
        "method": "POST",
        "query": "3",
        "header": "present",
        "body": {"value": 7},
    }


def test_versioned_route_uses_default_resolver_when_not_injected(tmp_path) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            request=request,
            json={"ok": True},
        )

    application = create_app(
        settings=Settings(
            local_demo_targets="http://localhost:8001",
            database_path=str(tmp_path / "default-resolver.sqlite3"),
        ),
        transport=httpx.MockTransport(handler),
    )
    with TestClient(application) as test_client:
        response = test_client.post(
            "/api/v1/requests/execute",
            json={
                "url": "http://localhost:8001/users",
                "method": "GET",
                "headers": {},
                "query_params": {},
                "timeout": 3,
            },
        )

    assert response.status_code == 200
    assert response.json()["body"] == {"ok": True}


def test_pinned_transport_uses_resolved_loopback_and_ignores_proxy_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def exercise() -> None:
        async def serve_one_request(
            reader: asyncio.StreamReader,
            writer: asyncio.StreamWriter,
        ) -> None:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Content-Length: 14\r\n"
                b"Connection: close\r\n\r\n"
                b'{"local":true}'
            )
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(
            serve_one_request,
            host="127.0.0.1",
            port=0,
        )
        port = server.sockets[0].getsockname()[1]

        async def resolver(host: str, requested_port: int) -> Sequence[str]:
            assert host == "localhost"
            assert requested_port == port
            return ("127.0.0.1",)

        service = ApiRequestService(
            Settings(allow_localhost_targets=True),
            resolver=resolver,
        )
        try:
            response = await service.execute(
                ApiRequest(url="http://localhost:{}/health".format(port))
            )
            assert response.status_code == 200
            assert response.body == {"local": True}
            assert response.is_json is True
        finally:
            await service.aclose()
            server.close()
            await server.wait_closed()

    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    asyncio.run(exercise())


def test_request_validation_rejects_credentials_and_oversized_json() -> None:
    with pytest.raises(ValueError):
        ApiRequest(url="https://user:password@target.test")

    with pytest.raises(ValueError):
        ApiRequest(url=TARGET_URL, body={"large": "x" * 1_048_577})

    with pytest.raises(ValueError):
        ApiRequest(url=TARGET_URL, headers={"Host": "127.0.0.1"})
