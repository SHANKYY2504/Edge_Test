import asyncio
import ipaddress
import json
import socket
import ssl
import time
from typing import (
    Any,
    Awaitable,
    Callable,
    Dict,
    Iterable,
    List,
    Optional,
    Sequence,
    Set,
    Tuple,
)
from urllib.parse import urlsplit

import certifi
import httpcore
import httpx
from httpcore._backends.anyio import AnyIOBackend
from pydantic import JsonValue

from app.core.config import Settings
from app.models.errors import ErrorDetail
from app.models.requests import ApiRequest
from app.models.responses import ApiResponse

METADATA_HOSTS = {
    "metadata",
    "metadata.google.internal",
    "metadata.azure.internal",
    "instance-data",
}
METADATA_IPS = {
    "100.100.100.200",
    "169.254.169.254",
    "169.254.170.2",
    "fd00:ec2::254",
}
LOCAL_DEMO_TARGETS = ("http://localhost:8001", "http://127.0.0.1:8001")


class OutboundPolicyError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class DNSResolutionError(Exception):
    """Raised when a destination hostname cannot be resolved safely."""


async def resolve_host(host: str, port: int) -> Sequence[str]:
    try:
        results = await asyncio.get_running_loop().getaddrinfo(
            host,
            port,
            type=socket.SOCK_STREAM,
        )
    except OSError as exception:
        raise DNSResolutionError from exception
    addresses = {result[4][0] for result in results}
    if not addresses:
        raise DNSResolutionError
    return tuple(addresses)


def _normalize_host(host: str) -> str:
    return host.strip("[]").rstrip(".").lower()


def _is_local_host(host: str) -> bool:
    normalized = _normalize_host(host)
    if normalized == "localhost" or normalized.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(normalized).is_loopback
    except ValueError:
        return False


def _validate_resolved_addresses(
    host: str,
    addresses: Iterable[str],
    *,
    allow_localhost: bool,
) -> Tuple[str, ...]:
    parsed: Set[str] = set()
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address.split("%", 1)[0])
        except ValueError as exception:
            raise OutboundPolicyError(
                "destination_not_allowed",
                "The destination resolved to an invalid IP address.",
            ) from exception
        if str(ip) in METADATA_IPS:
            raise OutboundPolicyError(
                "destination_not_allowed",
                "Cloud metadata destinations are not allowed.",
            )
        is_explicit_localhost = allow_localhost and _is_local_host(host)
        if is_explicit_localhost:
            if not ip.is_loopback:
                raise OutboundPolicyError(
                    "destination_not_allowed",
                    "The local development destination resolved outside loopback.",
                )
        elif not ip.is_global:
            raise OutboundPolicyError(
                "destination_not_allowed",
                "Private, reserved, and link-local destinations are not allowed.",
            )
        parsed.add(str(ip))
    if not parsed:
        raise DNSResolutionError
    return tuple(sorted(parsed))


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Connect only to addresses already resolved and checked for this host."""

    def __init__(self) -> None:
        self._delegate = AnyIOBackend()
        self._pinned_addresses: Dict[str, str] = {}

    def pin(self, host: str, addresses: Sequence[str]) -> None:
        normalized = _normalize_host(host)
        if not addresses:
            raise DNSResolutionError
        self._pinned_addresses[normalized] = addresses[0]

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: Optional[float] = None,
        local_address: Optional[str] = None,
        socket_options: Optional[Iterable[Tuple[int, int, int]]] = None,
    ) -> httpcore.AsyncNetworkStream:
        pinned_address = self._pinned_addresses.get(_normalize_host(host))
        if pinned_address is None:
            raise httpcore.ConnectError("Destination address was not pinned.")
        return await self._delegate.connect_tcp(
            host=pinned_address,
            port=port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        _path: str,
        _timeout: Optional[float] = None,
        _socket_options: Optional[Iterable[Tuple[int, int, int]]] = None,
    ) -> httpcore.AsyncNetworkStream:
        del _path, _timeout, _socket_options
        raise httpcore.ConnectError("Unix socket connections are disabled.")

    async def sleep(self, seconds: float) -> None:
        await self._delegate.sleep(seconds)


class PinnedHTTPTransport(httpx.AsyncHTTPTransport):
    def __init__(
        self,
        *,
        max_connections: int,
        max_keepalive_connections: int,
    ) -> None:
        limits = httpx.Limits(
            max_connections=max_connections,
            max_keepalive_connections=max_keepalive_connections,
        )
        super().__init__(trust_env=False, limits=limits)
        self.pinned_backend = PinnedNetworkBackend()
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(cafile=certifi.where()),
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            network_backend=self.pinned_backend,
            retries=0,
        )


class ApiRequestService:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        resolver: Optional[
            Callable[[str, int], Awaitable[Sequence[str]]]
        ] = None,
    ) -> None:
        self.settings = settings
        self._transport = transport or PinnedHTTPTransport(
            max_connections=settings.max_http_connections,
            max_keepalive_connections=settings.max_keepalive_connections,
        )
        self._pinned_backend = (
            self._transport.pinned_backend
            if isinstance(self._transport, PinnedHTTPTransport)
            else None
        )
        self._resolver = resolver or resolve_host
        self._semaphore = asyncio.Semaphore(settings.max_concurrent_requests)
        self._client = httpx.AsyncClient(
            transport=self._transport,
            timeout=httpx.Timeout(10.0),
            follow_redirects=False,
            trust_env=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def execute(self, api_request: ApiRequest) -> ApiResponse:
        started_at = time.perf_counter()
        try:
            return await asyncio.wait_for(
                self._execute_request(api_request, started_at),
                timeout=api_request.timeout,
            )
        except asyncio.TimeoutError:
            return self._failure(
                started_at,
                "timeout",
                "The request timed out.",
            )
        except OutboundPolicyError as exception:
            return self._failure(started_at, exception.code, exception.message)
        except DNSResolutionError:
            return self._failure(
                started_at,
                "dns_resolution_failed",
                "The destination hostname could not be resolved.",
            )
        except httpx.TimeoutException:
            return self._failure(
                started_at,
                "timeout",
                "The request timed out.",
            )
        except httpx.InvalidURL:
            return self._failure(
                started_at,
                "invalid_url",
                "The request URL is invalid.",
            )
        except httpx.ConnectError:
            return self._failure(
                started_at,
                "connection_error",
                "A connection to the destination could not be established.",
            )
        except httpx.RequestError:
            return self._failure(
                started_at,
                "transport_error",
                "The request could not be completed due to a network error.",
            )

    async def _execute_request(
        self,
        api_request: ApiRequest,
        started_at: float,
    ) -> ApiResponse:
        async with self._semaphore:
            parsed_url = urlsplit(str(api_request.url))
            scheme = parsed_url.scheme.lower()
            if scheme not in {"http", "https"} or not parsed_url.hostname:
                raise OutboundPolicyError(
                    "invalid_url",
                    "Only valid HTTP and HTTPS URLs are supported.",
                )
            host = _normalize_host(parsed_url.hostname)
            if host in METADATA_HOSTS or host.endswith(".metadata.google.internal"):
                raise OutboundPolicyError(
                    "destination_not_allowed",
                    "Cloud metadata destinations are not allowed.",
                )

            local_target = _is_local_host(host)
            explicitly_allowed = host in self.settings.allowed_outbound_hosts
            port = parsed_url.port or (443 if scheme == "https" else 80)
            normalized_target = "{}://{}:{}".format(scheme, host, port)
            local_demo_allowed = normalized_target in {
                target.rstrip("/")
                for target in self.settings.allowed_local_demo_targets
            }
            if local_target:
                if not (self.settings.allow_localhost_targets or local_demo_allowed):
                    raise OutboundPolicyError(
                        "destination_not_allowed",
                        "This localhost destination is not authorized.",
                    )
            elif not explicitly_allowed:
                raise OutboundPolicyError(
                    "destination_not_allowed",
                    "The destination host is not in the outbound allowlist.",
                )

            try:
                raw_addresses = await self._resolver(host, port)
            except DNSResolutionError:
                raise
            except OSError as exception:
                raise DNSResolutionError from exception
            addresses = _validate_resolved_addresses(
                host,
                raw_addresses,
                allow_localhost=(
                    self.settings.allow_localhost_targets or local_demo_allowed
                ),
            )
            if self._pinned_backend is not None:
                self._pinned_backend.pin(host, addresses)

            request_headers = httpx.Headers(api_request.headers)
            request_headers["accept-encoding"] = "identity"
            request_options: Dict[str, Any] = {
                "headers": request_headers,
                "timeout": httpx.Timeout(api_request.timeout),
                "follow_redirects": False,
            }
            if "body" in api_request.model_fields_set:
                request_options["json"] = api_request.body

            async with self._client.stream(
                api_request.method.value,
                httpx.URL(str(api_request.url)).copy_merge_params(
                    api_request.query_params
                ),
                **request_options,
            ) as response:
                content_type = response.headers.get("content-type")
                response_headers = dict(response.headers)
                content_encoding = response.headers.get(
                    "content-encoding", "identity"
                ).lower()
                if content_encoding not in {"", "identity"}:
                    return self._failure_response(
                        started_at,
                        "unsupported_content_encoding",
                        "Compressed response bodies are not supported.",
                        status_code=response.status_code,
                        headers=response_headers,
                        content_type=content_type,
                    )
                declared_size = response.headers.get("content-length")
                if declared_size is not None:
                    try:
                        if int(declared_size) > self.settings.max_response_bytes:
                            return self._response_too_large(
                                started_at,
                                response.status_code,
                                response_headers,
                                content_type,
                            )
                    except ValueError:
                        pass

                if response.is_stream_consumed:
                    raw_body = response.content
                    if len(raw_body) > self.settings.max_response_bytes:
                        return self._response_too_large(
                            started_at,
                            response.status_code,
                            response_headers,
                            content_type,
                        )
                else:
                    body_chunks: List[bytes] = []
                    body_size = 0
                    async for chunk in response.aiter_raw():
                        body_size += len(chunk)
                        if body_size > self.settings.max_response_bytes:
                            return self._response_too_large(
                                started_at,
                                response.status_code,
                                response_headers,
                                content_type,
                            )
                        body_chunks.append(chunk)
                    raw_body = b"".join(body_chunks)
                body, is_json = self._decode_body(raw_body, content_type)
                return ApiResponse(
                    status_code=response.status_code,
                    headers=response_headers,
                    body=body,
                    content_type=content_type,
                    is_json=is_json,
                    execution_time_ms=self._elapsed_ms(started_at),
                )

    @staticmethod
    def _decode_body(
        raw_body: bytes, content_type: Optional[str]
    ) -> Tuple[Optional[JsonValue], bool]:
        if not raw_body:
            return None, False
        charset = "utf-8"
        if content_type:
            for parameter in content_type.split(";")[1:]:
                name, separator, value = parameter.strip().partition("=")
                if separator and name.lower() == "charset":
                    charset = value.strip().strip("\"'") or "utf-8"
                    break
        try:
            text = raw_body.decode(charset, errors="replace")
        except LookupError:
            text = raw_body.decode("utf-8", errors="replace")
        try:
            parsed_body = json.loads(
                text,
                parse_constant=ApiRequestService._reject_json_constant,
            )
            return parsed_body, True
        except (json.JSONDecodeError, RecursionError, ValueError):
            return text, False

    @staticmethod
    def _reject_json_constant(value: str) -> None:
        raise ValueError("Non-standard JSON constant: {}".format(value))

    def _response_too_large(
        self,
        started_at: float,
        status_code: int,
        headers: Dict[str, str],
        content_type: Optional[str],
    ) -> ApiResponse:
        return ApiResponse(
            status_code=status_code,
            headers=headers,
            content_type=content_type,
            error=ErrorDetail(
                code="response_too_large",
                message="The response exceeded the configured size limit.",
            ),
            execution_time_ms=self._elapsed_ms(started_at),
        )

    def _failure_response(
        self,
        started_at: float,
        code: str,
        message: str,
        *,
        status_code: int,
        headers: Dict[str, str],
        content_type: Optional[str],
    ) -> ApiResponse:
        return ApiResponse(
            status_code=status_code,
            headers=headers,
            content_type=content_type,
            error=ErrorDetail(code=code, message=message),
            execution_time_ms=self._elapsed_ms(started_at),
        )

    def _failure(self, started_at: float, code: str, message: str) -> ApiResponse:
        return ApiResponse(
            error=ErrorDetail(code=code, message=message),
            execution_time_ms=self._elapsed_ms(started_at),
        )

    @staticmethod
    def _elapsed_ms(started_at: float) -> float:
        return round((time.perf_counter() - started_at) * 1000, 3)
