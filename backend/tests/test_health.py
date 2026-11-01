from fastapi.testclient import TestClient
from app.main import app, create_app

client = TestClient(app)


def test_health_endpoint_returns_service_status() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "EdgeTest API"}


def test_validation_errors_use_the_standard_error_envelope() -> None:
    with TestClient(app) as test_client:
        response = test_client.post(
            "/api/v1/requests/execute",
            json={"url": "not a URL", "method": "GET"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "validation_error",
            "message": "The request contains invalid data.",
        }
    }


def test_cors_allows_configured_local_frontend_origin() -> None:
    response = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 200
    assert (
        response.headers["access-control-allow-origin"]
        == "http://localhost:5173"
    )


def test_unexpected_errors_do_not_expose_internal_details() -> None:
    application = create_app()

    @application.get("/test/internal-error")
    async def internal_error() -> None:
        raise RuntimeError("sensitive internal details")

    test_client = TestClient(application, raise_server_exceptions=False)
    response = test_client.get("/test/internal-error")

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_server_error",
            "message": "An unexpected error occurred.",
        }
    }
    assert "sensitive internal details" not in response.text
