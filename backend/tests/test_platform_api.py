import json
import stat

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def build_app(
    tmp_path,
    handler,
    *,
    ai_handler=None,
    ai_key="",
    ai_provider="openai",
    gemini_key="",
    gemini_model="gemini-test-model",
    gemini_client=None,
):
    async def resolver(host: str, port: int):
        if host == "api.example.test":
            assert port == 443
        return ("93.184.216.34",)

    return create_app(
        settings=Settings(
            database_path=str(tmp_path / "platform.sqlite3"),
            outbound_allowed_hosts="api.example.test",
            ai_provider=ai_provider,
            ai_api_key=ai_key,
            ai_api_url="https://llm.example.test/v1/chat/completions",
            gemini_api_key=gemini_key,
            gemini_model=gemini_model,
        ),
        transport=httpx.MockTransport(handler),
        resolver=resolver,
        ai_transport=httpx.MockTransport(ai_handler) if ai_handler else None,
        gemini_client=gemini_client,
    )


def request_data():
    return {
        "url": "https://api.example.test/users?token=do-not-store",
        "method": "POST",
        "headers": {"Authorization": "Bearer do-not-store", "Content-Type": "application/json"},
        "query_params": {"access_token": "do-not-store"},
        "body": {"name": "Ada", "password": "do-not-store"},
        "timeout": 1,
    }


def test_gemini_settings_are_loaded_from_dotenv_file(tmp_path):
    env_file = tmp_path / "gemini.env"
    env_file.write_text(
        "AI_PROVIDER=gemini\nGEMINI_API_KEY=test-key\nGEMINI_MODEL=gemini-test\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=str(env_file))

    assert settings.ai_provider == "gemini"
    assert settings.gemini_api_key == "test-key"
    assert settings.gemini_model == "gemini-test"


def test_persists_collections_requests_failures_and_safe_replay(tmp_path):
    seen_requests = []

    async def handler(request: httpx.Request) -> httpx.Response:
        seen_requests.append(request)
        status = 500 if len(seen_requests) == 1 else 200
        return httpx.Response(
            status,
            request=request,
            headers={"content-type": "application/json"},
            json={"ok": status < 400, "token": "response-secret", "echo": "do-not-store"},
        )

    with TestClient(build_app(tmp_path, handler)) as client:
        collection = client.post("/api/v1/collections", json={"name": "Smoke tests"})
        assert collection.status_code == 201
        collection_id = collection.json()["id"]
        saved = client.post(
            "/api/v1/collections/{}/tests".format(collection_id),
            json={
                "name": "Create user",
                "request": request_data(),
                "test_cases": [{"label": "missing password"}],
            },
        )
        assert saved.status_code == 201
        saved_id = saved.json()["id"]
        assert saved.json()["request"]["headers"]["Authorization"] == "[REDACTED]"
        assert saved.json()["request"]["url"].endswith("token=%5BREDACTED%5D")
        assert saved.json()["request"]["query_params"]["access_token"] == "[REDACTED]"
        assert saved.json()["request"]["body"]["password"] == "[REDACTED]"
        assert saved.json()["test_cases"][0]["label"] == "missing password"

        executed = client.post(
            "/api/v1/requests/execute?collection_id={}&saved_test_id={}&test_case=missing".format(
                collection_id, saved_id
            ),
            json=request_data(),
        )
        assert executed.status_code == 200
        failures = client.get(
            "/api/v1/failures",
            params={"method": "POST", "status_code": 500, "error_type": "http_error"},
        )
        assert len(failures.json()) == 1
        original = failures.json()[0]
        assert original["request"]["headers"]["Authorization"] == "[REDACTED]"
        assert original["test_case"] == {"name": "missing"}
        assert original["response"]["body"]["token"] == "[REDACTED]"
        assert original["response"]["body"]["echo"] == "[REDACTED]"
        assert "do-not-store" not in json.dumps(original)

        refused = client.post(
            "/api/v1/executions/{}/replay".format(original["id"]),
            json={"confirmed": False},
        )
        assert refused.status_code == 409
        assert len(seen_requests) == 1
        replay = client.post(
            "/api/v1/executions/{}/replay".format(original["id"]),
            json={"confirmed": True},
        )
        assert replay.status_code == 200, replay.text
        assert replay.json()["original"]["id"] == original["id"]
        assert replay.json()["replay"]["replay_of"] == original["id"]
        assert replay.json()["replay"]["outcome"] == "passed"
        assert seen_requests[1].headers["authorization"] == "[REDACTED]"
        assert "token=%5BREDACTED%5D" in str(seen_requests[1].url)
        assert "access_token=%5BREDACTED%5D" in str(seen_requests[1].url)
        assert stat.S_IMODE((tmp_path / "platform.sqlite3").stat().st_mode) == 0o600

        dashboard = client.get("/api/v1/analytics").json()
        assert dashboard["total_requests"] == 2
        assert dashboard["total_test_cases"] == 2
        assert dashboard["saved_test_case_definitions"] == 1
        assert dashboard["failed_tests"] == 1
        assert dashboard["passed_tests"] == 1
        assert dashboard["failing_endpoints"][0]["failures"] == 1
        assert len(dashboard["trends"]) == 1

        csv_export = client.get("/api/v1/executions/export?format=csv")
        json_export = client.get("/api/v1/executions/export?format=json")
        assert csv_export.headers["content-type"].startswith("text/csv")
        assert "outcome" in csv_export.text
        assert len(json.loads(json_export.text)) == 2


def test_collections_can_be_renamed_and_deleted_with_saved_tests(tmp_path):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, request=request, json={"ok": True})

    with TestClient(build_app(tmp_path, handler)) as client:
        created = client.post("/api/v1/collections", json={"name": "Old"})
        collection_id = created.json()["id"]
        renamed = client.patch(
            "/api/v1/collections/{}".format(collection_id),
            json={"name": "New"},
        )
        assert renamed.json()["name"] == "New"
        assert client.get("/api/v1/collections").json()[0]["name"] == "New"
        assert client.delete("/api/v1/collections/{}".format(collection_id)).status_code == 204
        assert client.get("/api/v1/collections").json() == []
        survivor = client.post("/api/v1/collections", json={"name": "Survivor"})
        survivor_id = survivor.json()["id"]

    with TestClient(build_app(tmp_path, handler)) as restarted:
        restored = restarted.get("/api/v1/collections").json()
        assert [collection["id"] for collection in restored] == [survivor_id]


def test_ai_analysis_requires_consent_and_handles_provider_responses(tmp_path):
    calls = []

    async def request_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request, json={"error": "unavailable"})

    async def ai_handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        submitted = request.content.decode()
        assert "do-not-store" not in submitted
        return httpx.Response(
            200,
            request=request,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "possible_root_cause": "Validation rejected the payload.",
                                    "explanation": "The server returned an error.",
                                    "debugging_steps": ["Inspect request schema."],
                                    "potential_issues": ["Required field mismatch."],
                                    "suggested_corrections": ["Check required fields."],
                                }
                            )
                        }
                    }
                ]
            },
        )

    with TestClient(
        build_app(tmp_path, request_handler, ai_handler=ai_handler, ai_key="private-ai-key")
    ) as client:
        failed = client.post("/api/v1/requests/execute", json=request_data())
        assert failed.status_code == 200
        execution_id = client.get("/api/v1/failures").json()[0]["id"]
        refused = client.post(
            "/api/v1/executions/{}/analyze".format(execution_id),
            json={"consent": False},
        )
        assert refused.status_code == 400
        assert calls == []
        analysis = client.post(
            "/api/v1/executions/{}/analyze".format(execution_id),
            json={"consent": True},
        )
        assert analysis.status_code == 200
        assert analysis.json()["possible_root_cause"] == "Validation rejected the payload."
        assert analysis.json()["error_summary"] == "The server returned an error."
        assert analysis.json()["debugging_steps"] == ["Inspect request schema."]
        assert "private-ai-key" not in analysis.text
        assert calls[0].headers["authorization"] == "Bearer private-ai-key"


def test_ai_provider_failure_is_reported_without_provider_details(tmp_path):
    async def request_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request, json={"error": "failed"})

    async def ai_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request, text="sensitive provider diagnostic")

    with TestClient(
        build_app(tmp_path, request_handler, ai_handler=ai_handler, ai_key="configured")
    ) as client:
        client.post("/api/v1/requests/execute", json=request_data())
        execution_id = client.get("/api/v1/failures").json()[0]["id"]
        response = client.post(
            "/api/v1/executions/{}/analyze".format(execution_id),
            json={"consent": True},
        )
        assert response.status_code == 502
        assert "sensitive provider diagnostic" not in response.text


def test_gemini_analysis_uses_configured_model_sanitized_context_and_structured_result(
    tmp_path,
):
    calls = []

    class FakeResponse:
        text = json.dumps(
            {
                "error_summary": "The service returned a validation failure.",
                "possible_root_cause": "The payload failed server validation.",
                "explanation": "A required field may be missing.",
                "debugging_steps": ["Compare the request to the API schema."],
                "potential_issues": ["The payload shape may have changed."],
                "suggested_corrections": ["Include the required property."],
            }
        )

    class FakeModels:
        async def generate_content(self, *, model, contents, config):
            calls.append((model, contents, config))
            return FakeResponse()

    class FakeAio:
        models = FakeModels()

        async def aclose(self):
            return None

    class FakeGeminiClient:
        aio = FakeAio()

    async def request_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            422,
            request=request,
            headers={"content-type": "application/json"},
            json={"message": "invalid input", "secret": "response-secret"},
        )

    api_key = "gemini-secret-not-for-response"
    with TestClient(
        build_app(
            tmp_path,
            request_handler,
            ai_provider="gemini",
            gemini_key=api_key,
            gemini_model="gemini-custom",
            gemini_client=FakeGeminiClient(),
        )
    ) as client:
        client.post("/api/v1/requests/execute", json=request_data())
        execution_id = client.get("/api/v1/failures").json()[0]["id"]
        response = client.post(
            "/api/v1/executions/{}/analyze".format(execution_id),
            json={"consent": True},
        )

    assert response.status_code == 200
    result = response.json()
    assert result["provider"] == "gemini"
    assert result["model"] == "gemini-custom"
    assert result["error_summary"] == "The service returned a validation failure."
    assert result["suggested_corrections"] == ["Include the required property."]
    assert api_key not in response.text
    assert len(calls) == 1
    model, submitted, config = calls[0]
    context = json.loads(submitted)
    assert model == "gemini-custom"
    assert context["method"] == "POST"
    assert context["status_code"] == 422
    assert context["response_body"]["message"] == "invalid input"
    assert context["request"]["body"]["password"] == "[REDACTED]"
    assert "[REDACTED]" in context["request"]["headers"]["Authorization"]
    assert "do-not-store" not in submitted
    assert api_key not in submitted
    assert config.response_mime_type == "application/json"


def test_gemini_missing_key_is_reported_without_invoking_provider(tmp_path):
    async def request_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request, json={"error": "failed"})

    class UnexpectedGeminiClient:
        aio = None

    with TestClient(
        build_app(
            tmp_path,
            request_handler,
            ai_provider="gemini",
            gemini_client=UnexpectedGeminiClient(),
        )
    ) as client:
        client.post("/api/v1/requests/execute", json=request_data())
        execution_id = client.get("/api/v1/failures").json()[0]["id"]
        response = client.post(
            "/api/v1/executions/{}/analyze".format(execution_id),
            json={"consent": True},
        )

    assert response.status_code == 502
    assert "GEMINI_API_KEY is missing" in response.json()["error"]["message"]


@pytest.mark.parametrize(
    ("exception", "message"),
    [
        (type("ProviderError", (Exception,), {"code": 401})("secret diagnostic"), "Check GEMINI_API_KEY"),
        (type("ProviderError", (Exception,), {"code": 429})("secret diagnostic"), "quota or rate limit"),
        (type("ProviderError", (Exception,), {"code": 503})("secret diagnostic"), "provider availability"),
        (TimeoutError("secret diagnostic"), "timed out"),
    ],
)
def test_gemini_provider_errors_are_sanitized(
    tmp_path, exception, message
):
    class FakeModels:
        async def generate_content(self, **_kwargs):
            raise exception

    class FakeAio:
        models = FakeModels()

        async def aclose(self):
            return None

    class FakeGeminiClient:
        aio = FakeAio()

    async def request_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, request=request, json={"error": "failed"})

    with TestClient(
        build_app(
            tmp_path,
            request_handler,
            ai_provider="gemini",
            gemini_key="private-gemini-key",
            gemini_client=FakeGeminiClient(),
        )
    ) as client:
        client.post("/api/v1/requests/execute", json=request_data())
        execution_id = client.get("/api/v1/failures").json()[0]["id"]
        response = client.post(
            "/api/v1/executions/{}/analyze".format(execution_id),
            json={"consent": True},
        )

    assert response.status_code == 502
    assert message in response.json()["error"]["message"]
    assert "secret diagnostic" not in response.text
    assert "private-gemini-key" not in response.text
