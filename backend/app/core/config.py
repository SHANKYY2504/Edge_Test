from functools import lru_cache
from typing import List, Tuple

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "EdgeTest API"
    environment: str = "development"
    cors_allowed_origins: str = (
        "http://localhost:5173,http://127.0.0.1:5173"
    )
    outbound_allowed_hosts: str = ""
    allow_localhost_targets: bool = False
    local_demo_targets: str = (
        "http://localhost:8001,http://127.0.0.1:8001"
    )
    max_response_bytes: int = Field(default=1_048_576, ge=1_024, le=10_485_760)
    max_concurrent_requests: int = Field(default=20, ge=1, le=200)
    max_http_connections: int = Field(default=50, ge=1, le=500)
    max_keepalive_connections: int = Field(default=20, ge=0, le=200)
    database_path: str = "data/edgetest.sqlite3"
    ai_provider: str = "openai"
    ai_api_key: str = ""
    ai_api_url: str = "https://api.openai.com/v1/chat/completions"
    ai_model: str = "gpt-4o-mini"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def allowed_origins(self) -> List[str]:
        return [
            origin.strip()
            for origin in self.cors_allowed_origins.split(",")
            if origin.strip()
        ]

    @property
    def allowed_outbound_hosts(self) -> Tuple[str, ...]:
        return tuple(
            host.strip()
            .strip("[]")
            .rstrip(".")
            .encode("idna")
            .decode("ascii")
            .lower()
            for host in self.outbound_allowed_hosts.split(",")
            if host.strip()
        )

    @property
    def allowed_local_demo_targets(self) -> Tuple[str, ...]:
        return tuple(
            target.strip().rstrip("/")
            for target in self.local_demo_targets.split(",")
            if target.strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
