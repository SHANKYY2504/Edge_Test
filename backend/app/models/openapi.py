from typing import Dict, List, Optional

from pydantic import (
    AnyHttpUrl,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

from app.models.boundary import BoundaryTestCase
from app.models.requests import ApiRequest, HttpMethod

MAX_OPENAPI_SPEC_BYTES = 1_048_576


class OpenApiImportRequest(BaseModel):
    specification: Optional[str] = Field(default=None, max_length=MAX_OPENAPI_SPEC_BYTES)
    url: Optional[AnyHttpUrl] = None
    base_url: Optional[AnyHttpUrl] = None

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="after")
    def validate_source(self) -> "OpenApiImportRequest":
        if (self.specification is None) == (self.url is None):
            raise ValueError("Provide either specification text or a URL, but not both.")
        if self.url is not None and (
            self.url.username is not None or self.url.password is not None
        ):
            raise ValueError("Specification URLs containing credentials are not allowed.")
        if self.base_url is not None:
            if self.base_url.username is not None or self.base_url.password is not None:
                raise ValueError("Base URLs containing credentials are not allowed.")
            if self.base_url.query is not None or self.base_url.fragment is not None:
                raise ValueError("Base URLs must not contain a query or fragment.")
        return self


class OpenApiParameter(BaseModel):
    name: str
    location: str
    required: bool
    schema_value: JsonValue = Field(alias="schema")

    model_config = ConfigDict(populate_by_name=True)


class OpenApiSchema(BaseModel):
    name: str
    schema_value: JsonValue = Field(alias="schema")

    model_config = ConfigDict(populate_by_name=True)


class OpenApiOperation(BaseModel):
    id: str
    method: HttpMethod
    path: str
    summary: str
    tags: List[str] = Field(default_factory=list)
    request: ApiRequest
    parameters: List[OpenApiParameter] = Field(default_factory=list)
    request_body_schema: Optional[JsonValue] = None
    cases: List[BoundaryTestCase] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class OpenApiImportResponse(BaseModel):
    title: str
    version: str
    specification_version: str
    operations: List[OpenApiOperation]
    servers: List[str] = Field(default_factory=list)
    schemas: List[OpenApiSchema] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class OpenApiImportTextRequest(BaseModel):
    specification: str = Field(min_length=1, max_length=MAX_OPENAPI_SPEC_BYTES)

    model_config = ConfigDict(extra="forbid")


class OpenApiRunResult(BaseModel):
    case_id: str
    request: Dict[str, JsonValue]
    response: Optional[Dict[str, JsonValue]] = None
    status_code: Optional[int] = None
    execution_time_ms: Optional[float] = None
    outcome: str
