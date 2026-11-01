from contextlib import asynccontextmanager
import logging
import sqlite3
from typing import Any, AsyncIterator, Awaitable, Callable, Optional, Sequence

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.health import router as health_router
from app.api.v1.router import api_v1_router
from app.api.v1.router import platform_router
from app.core.config import Settings, get_settings
from app.models.errors import ErrorDetail, ErrorResponse
from app.services.request_execution import ApiRequestService
from app.services.ai_debugging import AiDebuggingService
from app.services.persistence import PlatformRepository

logger = logging.getLogger(__name__)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    response = ErrorResponse(error=ErrorDetail(code=code, message=message))
    return JSONResponse(status_code=status_code, content=response.model_dump())


def create_app(
    settings: Optional[Settings] = None,
    transport: Optional[httpx.AsyncBaseTransport] = None,
    resolver: Optional[Callable[[str, int], Awaitable[Sequence[str]]]] = None,
    ai_transport: Optional[httpx.AsyncBaseTransport] = None,
    gemini_client: Optional[Any] = None,
) -> FastAPI:
    app_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        request_service = ApiRequestService(
            app_settings,
            transport=transport,
            resolver=resolver,
        )
        application.state.request_service = request_service
        repository = PlatformRepository(app_settings.database_path)
        repository.initialize()
        application.state.platform_repository = repository
        application.state.ai_service = AiDebuggingService(
            app_settings,
            transport=ai_transport,
            gemini_client=gemini_client,
        )
        try:
            yield
        finally:
            await application.state.ai_service.aclose()
            await request_service.aclose()

    application = FastAPI(title=app_settings.app_name, lifespan=lifespan)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=app_settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @application.exception_handler(HTTPException)
    async def handle_http_exception(
        _request: Request, exception: HTTPException
    ) -> JSONResponse:
        del _request
        message = (
            exception.detail
            if isinstance(exception.detail, str)
            else "The request could not be completed."
        )
        return _error_response(
            exception.status_code,
            "http_error",
            message,
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_exception(
        _request: Request, _exception: RequestValidationError
    ) -> JSONResponse:
        del _request, _exception
        return _error_response(
            422,
            "validation_error",
            "The request contains invalid data.",
        )

    @application.exception_handler(sqlite3.Error)
    async def handle_database_exception(
        _request: Request, _exception: sqlite3.Error
    ) -> JSONResponse:
        del _request, _exception
        return _error_response(
            503,
            "database_unavailable",
            "Persistent storage is unavailable.",
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_exception(
        _request: Request, _exception: Exception
    ) -> JSONResponse:
        del _request, _exception
        logger.exception("Unhandled API exception")
        return _error_response(
            500,
            "internal_server_error",
            "An unexpected error occurred.",
        )

    application.include_router(health_router)
    application.include_router(api_v1_router, prefix="/api/v1")
    application.include_router(platform_router, prefix="/api/v1")
    return application


app = create_app()
