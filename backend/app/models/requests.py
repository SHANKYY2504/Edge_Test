import json
from enum import Enum
from typing import Dict, Optional

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
)

from app.core.constants import (
    MAX_REQUEST_BODY_BYTES,
    MAX_REQUEST_HEADER_BYTES,
    MAX_REQUEST_HEADERS,
    MAX_REQUEST_TIMEOUT_SECONDS,
    MIN_REQUEST_TIMEOUT_SECONDS,
    MAX_REQUEST_URL_LENGTH,
)


class HttpMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"
    HEAD = "HEAD"
    OPTIONS = "OPTIONS"


class ApiRequest(BaseModel):
    url: AnyHttpUrl = Field(max_length=MAX_REQUEST_URL_LENGTH)
    method: HttpMethod = HttpMethod.GET
    headers: Dict[str, str] = Field(default_factory=dict)
    query_params: Dict[str, str] = Field(default_factory=dict)
    body: Optional[JsonValue] = None
    timeout: float = Field(
        default=30.0,
        ge=MIN_REQUEST_TIMEOUT_SECONDS,
        le=MAX_REQUEST_TIMEOUT_SECONDS,
    )

    model_config = ConfigDict(extra="forbid")

    @field_validator("method", mode="before")
    @classmethod
    def normalize_method(cls, value: object) -> object:
        if isinstance(value, str):
            return value.upper()
        return value

    @field_validator("url")
    @classmethod
    def reject_url_credentials(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.username is not None or value.password is not None:
            raise ValueError("URL credentials are not allowed.")
        return value

    @field_validator("headers")
    @classmethod
    def validate_headers(cls, value: Dict[str, str]) -> Dict[str, str]:
        if len(value) > MAX_REQUEST_HEADERS:
            raise ValueError("Too many request headers.")
        if any(name.lower() in {"host", "proxy-authorization", "proxy-connection"} for name in value):
            raise ValueError("Host and proxy control headers are not allowed.")
        total_size = sum(
            len(name.encode("utf-8")) + len(content.encode("utf-8"))
            for name, content in value.items()
        )
        if total_size > MAX_REQUEST_HEADER_BYTES:
            raise ValueError("Request headers exceed the size limit.")
        return value

    @field_validator("body")
    @classmethod
    def limit_json_body_size(cls, value: Optional[JsonValue]) -> Optional[JsonValue]:
        if value is not None:
            encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
            if len(encoded.encode("utf-8")) > MAX_REQUEST_BODY_BYTES:
                raise ValueError("JSON request body exceeds the size limit.")
        return value
