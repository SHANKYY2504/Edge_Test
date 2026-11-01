import pytest
from pydantic import ValidationError

from app.models.requests import ApiRequest, HttpMethod
from app.models.responses import ApiResponse


def test_request_defaults_and_lowercase_method_normalization() -> None:
    request = ApiRequest(url="https://example.com/api", method="post")

    assert request.method is HttpMethod.POST
    assert request.headers == {}
    assert request.query_params == {}
    assert request.timeout == 30.0


def test_request_accepts_json_body_and_headers() -> None:
    request = ApiRequest(
        url="https://example.com/api",
        method="POST",
        headers={"content-type": "application/json"},
        query_params={"page": "1"},
        body={"items": [1, "two", None]},
        timeout=1.5,
    )

    assert request.body == {"items": [1, "two", None]}
    assert request.headers["content-type"] == "application/json"
    assert request.query_params == {"page": "1"}
    assert request.timeout == 1.5


@pytest.mark.parametrize(
    "url",
    ["not a URL", "ftp://example.com/file", "https://"],
)
def test_request_rejects_invalid_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        ApiRequest(url=url)


def test_request_rejects_unsupported_method() -> None:
    with pytest.raises(ValidationError):
        ApiRequest(url="https://example.com", method="TRACE")


@pytest.mark.parametrize("timeout", [0.09, 120.01])
def test_request_rejects_timeout_outside_supported_range(timeout: float) -> None:
    with pytest.raises(ValidationError):
        ApiRequest(url="https://example.com", timeout=timeout)


def test_request_headers_use_independent_defaults() -> None:
    first = ApiRequest(url="https://example.com")
    second = ApiRequest(url="https://example.com")
    first.headers["x-test"] = "value"

    assert second.headers == {}


def test_response_model_supports_execution_result_and_error() -> None:
    response = ApiResponse(
        status_code=200,
        headers={"content-type": "application/json"},
        body={"ok": True},
        content_type="application/json",
        is_json=True,
        execution_time_ms=12.4,
    )

    assert response.status_code == 200
    assert response.body == {"ok": True}
    assert response.is_json is True
    assert response.execution_time_ms == 12.4
    assert response.error is None

    error_response = ApiResponse(
        error={"code": "timeout", "message": "The request timed out."}
    )
    assert error_response.error is not None
    assert error_response.error.code == "timeout"
