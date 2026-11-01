from typing import Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.requests import ApiRequest


class CollectionInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)

    model_config = ConfigDict(extra="forbid")


class SavedTestInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    request: ApiRequest
    test_cases: List[Dict[str, object]] = Field(default_factory=list, max_length=500)

    model_config = ConfigDict(extra="forbid")


class AiAnalysisRequest(BaseModel):
    consent: bool

    model_config = ConfigDict(extra="forbid")


class ReplayRequest(BaseModel):
    confirmed: bool = False

    model_config = ConfigDict(extra="forbid")


class AiAnalysisResponse(BaseModel):
    error_summary: str
    possible_root_cause: str
    explanation: str
    debugging_steps: List[str]
    potential_issues: List[str]
    suggested_corrections: List[str]
    provider: str
    model: str


class ExecutionRecord(BaseModel):
    id: str
    collection_id: Optional[str] = None
    saved_test_id: Optional[str] = None
    replay_of: Optional[str] = None
    method: str
    endpoint: str
    status_code: Optional[int] = None
    outcome: str
    error_category: Optional[str] = None
    duration_ms: Optional[float] = None
    test_case: Optional[Dict[str, object]] = None
    request: Dict[str, object]
    response: Dict[str, object]
    created_at: str


class DashboardResponse(BaseModel):
    total_requests: int
    total_test_cases: int
    saved_test_case_definitions: int
    passed_tests: int
    failed_tests: int
    errored_tests: int
    recent_failures: List[ExecutionRecord]
    failing_endpoints: List[Dict[str, object]]
    trends: List[Dict[str, object]]
