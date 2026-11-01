from fastapi import Request

from app.services.ai_debugging import AiDebuggingService
from app.services.request_execution import ApiRequestService
from app.services.persistence import PlatformRepository


def get_request_service(request: Request) -> ApiRequestService:
    return request.app.state.request_service


def get_platform_repository(request: Request) -> PlatformRepository:
    return request.app.state.platform_repository


def get_ai_service(request: Request) -> AiDebuggingService:
    return request.app.state.ai_service
