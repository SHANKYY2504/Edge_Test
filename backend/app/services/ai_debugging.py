import asyncio
import json
from typing import Any, Dict, List, Optional
from urllib.parse import urlsplit

import httpx
from google import genai
from google.genai import types

from app.core.config import Settings

ANALYSIS_SYSTEM_PROMPT = (
    "Analyze a failed API test. The supplied request and response have been "
    "sanitized. Return only a JSON object with string error_summary, string "
    "possible_root_cause, string explanation, string-array debugging_steps, "
    "string-array potential_issues, and string-array suggested_corrections. "
    "Be cautious and label uncertainty; suggestions are recommendations, "
    "not guaranteed diagnoses."
)
GEMINI_TIMEOUT_SECONDS = 20.0


class AiProviderError(Exception):
    pass


class AiDebuggingService:
    def __init__(
        self,
        settings: Settings,
        transport: Optional[httpx.AsyncBaseTransport] = None,
        gemini_client: Optional[Any] = None,
    ) -> None:
        self.settings = settings
        self.transport = transport
        self._gemini_client = gemini_client
        self._owns_gemini_client = False

    @property
    def provider_name(self) -> str:
        return self.settings.ai_provider.strip().lower()

    @property
    def model_name(self) -> str:
        if self.provider_name == "gemini":
            return self.settings.gemini_model
        return self.settings.ai_model

    async def analyze(self, execution: Dict[str, Any]) -> Dict[str, Any]:
        if self.provider_name == "gemini":
            result = await self._analyze_gemini(execution)
        elif self.provider_name in {"openai", "openai-compatible"}:
            result = await self._analyze_openai_compatible(execution)
        else:
            raise AiProviderError("The configured AI provider is not supported.")
        return _normalize_analysis(result)

    async def _analyze_openai_compatible(
        self, execution: Dict[str, Any]
    ) -> Dict[str, Any]:
        if not self.settings.ai_api_key:
            raise AiProviderError("AI debugging is not configured on this server.")
        provider_url = urlsplit(self.settings.ai_api_url)
        if provider_url.scheme != "https" or not provider_url.hostname:
            raise AiProviderError("The configured AI provider URL is invalid.")

        payload = {
            "model": self.settings.ai_model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": ANALYSIS_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(_execution_context(execution), ensure_ascii=True),
                },
            ],
        }
        try:
            async with httpx.AsyncClient(
                timeout=GEMINI_TIMEOUT_SECONDS,
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                async with client.stream(
                    "POST",
                    self.settings.ai_api_url,
                    headers={"Authorization": "Bearer " + self.settings.ai_api_key},
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        if len(content) + len(chunk) > 256_000:
                            raise AiProviderError(
                                "The AI provider returned an oversized response."
                            )
                        content.extend(chunk)
                    data = json.loads(content)
        except AiProviderError:
            raise
        except httpx.TimeoutException as exception:
            raise AiProviderError("The AI provider request timed out.") from exception
        except (httpx.HTTPError, ValueError):
            raise AiProviderError("The AI provider could not complete the analysis.")

        try:
            content = data["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise ValueError
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (KeyError, IndexError, TypeError, ValueError):
            raise AiProviderError("The AI provider returned an unsupported analysis format.")

    async def _analyze_gemini(self, execution: Dict[str, Any]) -> Dict[str, Any]:
        if not self.settings.gemini_api_key:
            raise AiProviderError(
                "Gemini debugging is not configured: GEMINI_API_KEY is missing."
            )
        client = self._gemini_client
        if client is None:
            client = genai.Client(
                api_key=self.settings.gemini_api_key,
                http_options=types.HttpOptions(timeout=20_000),
            )
            self._gemini_client = client
            self._owns_gemini_client = True
        try:
            response = await asyncio.wait_for(
                client.aio.models.generate_content(
                    model=self.settings.gemini_model,
                    contents=json.dumps(_execution_context(execution), ensure_ascii=True),
                    config=types.GenerateContentConfig(
                        system_instruction=ANALYSIS_SYSTEM_PROMPT,
                        temperature=0.2,
                        response_mime_type="application/json",
                    ),
                ),
                timeout=GEMINI_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, TimeoutError) as exception:
            raise AiProviderError("Gemini analysis timed out.") from exception
        except Exception as exception:
            raise AiProviderError(_gemini_error_message(exception)) from exception

        try:
            text = response.text
            if not isinstance(text, str) or not text.strip():
                raise ValueError
            result = json.loads(text)
            if not isinstance(result, dict):
                raise ValueError
            return result
        except (AttributeError, TypeError, ValueError):
            raise AiProviderError("Gemini returned an unsupported analysis format.")

    async def aclose(self) -> None:
        if self._owns_gemini_client and self._gemini_client is not None:
            await self._gemini_client.aio.aclose()
            self._gemini_client = None
            self._owns_gemini_client = False


def _execution_context(execution: Dict[str, Any]) -> Dict[str, Any]:
    request = execution.get("request")
    response = execution.get("response")
    response_data = response if isinstance(response, dict) else {}
    request_data = request if isinstance(request, dict) else {}
    return {
        "method": execution.get("method"),
        "url": execution.get("endpoint"),
        "status_code": execution.get("status_code", response_data.get("status_code")),
        "response_body": response_data.get("body"),
        "outcome": execution.get("outcome"),
        "error_category": execution.get("error_category"),
        "duration_ms": execution.get("duration_ms"),
        "request": request_data,
        "response_headers": response_data.get("headers"),
        "content_type": response_data.get("content_type"),
        "execution_error": response_data.get("error"),
        "created_at": execution.get("created_at"),
        "test_case": execution.get("test_case"),
    }


def _gemini_error_message(exception: Exception) -> str:
    code = getattr(exception, "code", None)
    if code is None:
        code = getattr(exception, "status_code", None)
    normalized_code = str(code).upper()
    if normalized_code in {"400", "401", "403", "INVALID_ARGUMENT", "UNAUTHENTICATED", "PERMISSION_DENIED"}:
        return "Gemini rejected the API key or request. Check GEMINI_API_KEY and provider access."
    if normalized_code in {"429", "RESOURCE_EXHAUSTED"}:
        return "Gemini quota or rate limit was exceeded. Try again later or check the project quota."
    if normalized_code in {"408", "504", "DEADLINE_EXCEEDED", "TIMEOUT"} or isinstance(
        exception, httpx.TimeoutException
    ):
        return "Gemini analysis timed out."
    return "Gemini could not complete the analysis. Check provider availability and configuration."


def _normalize_analysis(data: Dict[str, Any]) -> Dict[str, Any]:
    try:
        explanation = _text(data, "explanation")
        return {
            "error_summary": _text(data, "error_summary", default=explanation),
            "possible_root_cause": _text(data, "possible_root_cause"),
            "explanation": explanation,
            "debugging_steps": _strings(data, "debugging_steps"),
            "potential_issues": _strings(data, "potential_issues"),
            "suggested_corrections": _strings(data, "suggested_corrections"),
        }
    except ValueError as exception:
        raise AiProviderError("The AI provider returned an unsupported analysis format.") from exception


def _text(data: Dict[str, Any], key: str, default: Optional[str] = None) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise ValueError
    return value.strip()[:4000]


def _strings(data: Dict[str, Any], key: str) -> List[str]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError
    return [item[:1000] for item in value[:12]]
