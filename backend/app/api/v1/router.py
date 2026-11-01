import json
import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from app.api.dependencies import (
    get_ai_service,
    get_platform_repository,
    get_request_service,
)
from app.models.boundary import (
    BoundaryGenerationRequest,
    BoundaryGenerationResponse,
)
from app.models.openapi import OpenApiImportRequest, OpenApiImportResponse
from app.models.requests import ApiRequest
from app.models.responses import ApiResponse
from app.models.platform import (
    AiAnalysisResponse,
    AiAnalysisRequest,
    CollectionInput,
    ReplayRequest,
    SavedTestInput,
)
from app.services.ai_debugging import AiDebuggingService, AiProviderError
from app.services.boundary_generation import generate_boundary_cases
from app.services.openapi_import import OpenApiImportError, import_openapi_text
from app.services.persistence import PlatformRepository
from app.services.request_execution import ApiRequestService

api_v1_router = APIRouter()
platform_router = APIRouter()


@api_v1_router.post(
    "/requests/execute",
    response_model=ApiResponse,
    status_code=status.HTTP_200_OK,
    tags=["requests"],
)
async def execute_api_request(
    request: ApiRequest,
    service: ApiRequestService = Depends(get_request_service),
    repository: PlatformRepository = Depends(get_platform_repository),
    collection_id: Optional[str] = None,
    saved_test_id: Optional[str] = None,
    test_case: Optional[str] = None,
) -> ApiResponse:
    result = await service.execute(request)
    try:
        repository.record_execution(
            request,
            result,
            collection_id=collection_id,
            saved_test_id=saved_test_id,
            test_case={"name": test_case} if test_case else None,
        )
    except sqlite3.Error as exception:
        raise HTTPException(
            status_code=503, detail="The execution result could not be saved."
        ) from exception
    return result


@api_v1_router.post(
    "/boundary/generate",
    response_model=BoundaryGenerationResponse,
    status_code=status.HTTP_200_OK,
    tags=["boundary testing"],
)
async def generate_boundary_test_cases(
    request: BoundaryGenerationRequest,
) -> BoundaryGenerationResponse:
    return BoundaryGenerationResponse(cases=generate_boundary_cases(request.inputs))


@api_v1_router.post(
    "/openapi/import",
    response_model=OpenApiImportResponse,
    status_code=status.HTTP_200_OK,
    tags=["OpenAPI"],
)
async def import_openapi_specification(
    request: OpenApiImportRequest,
    service: ApiRequestService = Depends(get_request_service),
) -> OpenApiImportResponse:
    try:
        if request.specification is not None:
            specification_text = request.specification
        else:
            assert request.url is not None
            fetched = await service.execute(
                ApiRequest(
                    url=str(request.url),
                    method="GET",
                    headers={"Accept": "application/json, application/yaml, text/yaml, */*"},
                    timeout=10,
                )
            )
            if fetched.error is not None:
                raise OpenApiImportError(
                    "Specification URL could not be fetched: {}".format(
                        fetched.error.message
                    )
                )
            if fetched.status_code is None or not 200 <= fetched.status_code < 300:
                raise OpenApiImportError(
                    "Specification URL returned HTTP {}.".format(
                        fetched.status_code or "no status"
                    )
                )
            if isinstance(fetched.body, str):
                specification_text = fetched.body
            elif isinstance(fetched.body, (dict, list)):
                specification_text = json.dumps(fetched.body)
            else:
                raise OpenApiImportError(
                    "Specification URL did not return JSON or YAML text."
                )
        return import_openapi_text(
            specification_text,
            base_url=str(request.base_url) if request.base_url is not None else None,
        )
    except OpenApiImportError as exception:
        raise HTTPException(status_code=422, detail=str(exception)) from exception


@platform_router.get("/collections")
def list_collections(
    repository: PlatformRepository = Depends(get_platform_repository),
):
    return repository.list_collections()


@platform_router.post("/collections", status_code=status.HTTP_201_CREATED)
def create_collection(
    payload: CollectionInput,
    repository: PlatformRepository = Depends(get_platform_repository),
):
    try:
        return repository.create_collection(payload.name)
    except sqlite3.Error as exception:
        raise HTTPException(status_code=503, detail="Collection could not be saved.") from exception


@platform_router.patch("/collections/{collection_id}")
def rename_collection(
    collection_id: str,
    payload: CollectionInput,
    repository: PlatformRepository = Depends(get_platform_repository),
):
    collection = repository.rename_collection(collection_id, payload.name)
    if collection is None:
        raise HTTPException(status_code=404, detail="Collection not found.")
    return collection


@platform_router.delete("/collections/{collection_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_collection(
    collection_id: str,
    repository: PlatformRepository = Depends(get_platform_repository),
):
    if not repository.delete_collection(collection_id):
        raise HTTPException(status_code=404, detail="Collection not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@platform_router.get("/collections/{collection_id}/tests")
def list_saved_tests(
    collection_id: str,
    repository: PlatformRepository = Depends(get_platform_repository),
):
    return repository.list_saved_tests(collection_id)


@platform_router.post("/collections/{collection_id}/tests", status_code=status.HTTP_201_CREATED)
def save_test(
    collection_id: str,
    payload: SavedTestInput,
    repository: PlatformRepository = Depends(get_platform_repository),
):
    try:
        saved = repository.save_test(
            collection_id, payload.name, payload.request, payload.test_cases
        )
    except sqlite3.Error as exception:
        raise HTTPException(status_code=503, detail="Test could not be saved.") from exception
    if saved is None:
        raise HTTPException(status_code=404, detail="Collection not found.")
    return saved


@platform_router.delete("/tests/{test_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved_test(
    test_id: str,
    repository: PlatformRepository = Depends(get_platform_repository),
):
    if not repository.delete_saved_test(test_id):
        raise HTTPException(status_code=404, detail="Saved test not found.")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@platform_router.post("/tests/{test_id}/execute")
async def execute_saved_test(
    test_id: str,
    payload: ReplayRequest,
    repository: PlatformRepository = Depends(get_platform_repository),
    service: ApiRequestService = Depends(get_request_service),
):
    saved = repository.get_saved_test(test_id)
    if saved is None:
        raise HTTPException(status_code=404, detail="Saved test not found.")
    try:
        request = ApiRequest.model_validate(saved["request"])
    except ValueError as exception:
        raise HTTPException(status_code=422, detail="The saved request is no longer valid.") from exception
    if request.method.value not in {"GET", "HEAD", "OPTIONS"} and not payload.confirmed:
        raise HTTPException(status_code=409, detail="Confirmation is required for this request method.")
    response = await service.execute(request)
    try:
        record = repository.record_execution(
            request,
            response,
            collection_id=saved["collection_id"],
            saved_test_id=test_id,
            test_case={"name": "saved request smoke execution"},
        )
    except sqlite3.Error as exception:
        raise HTTPException(status_code=503, detail="Execution result could not be saved.") from exception
    return {"execution": record}


@platform_router.get("/executions")
def list_executions(
    endpoint: Optional[str] = None,
    method: Optional[str] = None,
    status_code: Optional[int] = None,
    error_type: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    repository: PlatformRepository = Depends(get_platform_repository),
):
    return repository.list_executions(
        endpoint=endpoint,
        method=method,
        status_code=status_code,
        error_type=error_type,
        limit=limit,
    )


@platform_router.get("/failures")
def list_failures(
    endpoint: Optional[str] = None,
    method: Optional[str] = None,
    status_code: Optional[int] = None,
    error_type: Optional[str] = None,
    limit: int = Query(default=100, ge=1, le=500),
    repository: PlatformRepository = Depends(get_platform_repository),
):
    return repository.list_executions(
        failures_only=True,
        endpoint=endpoint,
        method=method,
        status_code=status_code,
        error_type=error_type,
        limit=limit,
    )


@platform_router.post("/executions/{execution_id}/replay")
async def replay_execution(
    execution_id: str,
    payload: ReplayRequest,
    repository: PlatformRepository = Depends(get_platform_repository),
    service: ApiRequestService = Depends(get_request_service),
):
    original = repository.get_execution(execution_id)
    if original is None:
        raise HTTPException(status_code=404, detail="Execution not found.")
    try:
        request = ApiRequest.model_validate(original["request"])
    except ValueError as exception:
        raise HTTPException(status_code=422, detail="The stored request is no longer valid.") from exception
    if request.method.value not in {"GET", "HEAD", "OPTIONS"} and not payload.confirmed:
        raise HTTPException(status_code=409, detail="Confirmation is required for this request method.")
    response = await service.execute(request)
    try:
        replay = repository.record_execution(
            request,
            response,
            collection_id=original["collection_id"],
            saved_test_id=original["saved_test_id"],
            replay_of=execution_id,
            test_case=original["test_case"],
        )
    except sqlite3.Error as exception:
        raise HTTPException(status_code=503, detail="Replay result could not be saved.") from exception
    return {"original": original, "replay": replay}


@platform_router.post(
    "/executions/{execution_id}/analyze",
    response_model=AiAnalysisResponse,
)
async def analyze_failure(
    execution_id: str,
    payload: AiAnalysisRequest,
    repository: PlatformRepository = Depends(get_platform_repository),
    ai_service: AiDebuggingService = Depends(get_ai_service),
):
    if not payload.consent:
        raise HTTPException(status_code=400, detail="Consent is required before external analysis.")
    execution = repository.get_execution(execution_id)
    if execution is None:
        raise HTTPException(status_code=404, detail="Execution not found.")
    if execution["outcome"] == "passed":
        raise HTTPException(status_code=400, detail="Only failed executions can be analyzed.")
    try:
        analysis = await ai_service.analyze(execution)
    except AiProviderError as exception:
        raise HTTPException(status_code=502, detail=str(exception)) from exception
    return {
        **analysis,
        "provider": ai_service.provider_name,
        "model": ai_service.model_name,
    }


@platform_router.get("/executions/export")
def export_executions(
    format: str = Query(default="json", pattern="^(json|csv)$"),
    repository: PlatformRepository = Depends(get_platform_repository),
):
    content = repository.export(as_csv=format == "csv")
    media_type = "text/csv" if format == "csv" else "application/json"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": 'attachment; filename="edgetest-results.{}"'.format(format)},
    )


@platform_router.get("/analytics")
def analytics(
    repository: PlatformRepository = Depends(get_platform_repository),
):
    return repository.dashboard()
