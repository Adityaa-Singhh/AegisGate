"""AegisGate application configuration.

Loaded once at import time from environment variables and an optional .env
file.  All downstream modules should import the ``settings`` singleton rather
than constructing a new ``Settings`` instance.
"""

from __future__ import annotations

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Central configuration for the AegisGate platform.

    Values are resolved in this order (highest priority first):
    1. Actual environment variables
    2. Variables declared in the ``.env`` file at the project root
    3. Field defaults defined below
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ---------------------------------------------------------------------------
    # Runtime environment
    # ---------------------------------------------------------------------------
    ENVIRONMENT: str = "local"

    # ---------------------------------------------------------------------------
    # Persistence
    # ---------------------------------------------------------------------------
    DATABASE_URL: str = (
        "postgresql+asyncpg://aegis:aegis_password@localhost:5432/aegis_db"
    )
    REDIS_URL: str = "redis://localhost:6379/0"

    # ---------------------------------------------------------------------------
    # External LLM API keys  (empty → app starts without real credentials)
    # ---------------------------------------------------------------------------
    GROQ_API_KEY: str = ""
    OPENROUTER_API_KEY: str = ""

    # ---------------------------------------------------------------------------
    # Observability
    # ---------------------------------------------------------------------------
    LOG_LEVEL: str = "INFO"

    # ---------------------------------------------------------------------------
    # Outbound HTTP client tuning
    # ---------------------------------------------------------------------------
    HTTP_CLIENT_TIMEOUT: float = 2.0
    HTTP_MAX_KEEPALIVE: int = 50
    HTTP_MAX_CONNECTIONS: int = 200

    # ---------------------------------------------------------------------------
    # Computed properties
    # ---------------------------------------------------------------------------
    @computed_field  # type: ignore[misc]
    @property
    def is_production(self) -> bool:
        """Return ``True`` when the runtime environment is production."""
        return self.ENVIRONMENT == "production"


settings: Settings = Settings()
