from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from app.models.errors import ErrorDetail


class ApiResponse(BaseModel):
    status_code: Optional[int] = None
    headers: Dict[str, str] = Field(default_factory=dict)
    body: Optional[JsonValue] = None
    content_type: Optional[str] = None
    is_json: bool = False
    execution_time_ms: Optional[float] = Field(default=None, ge=0)
    error: Optional[ErrorDetail] = None

    model_config = ConfigDict(extra="forbid")
