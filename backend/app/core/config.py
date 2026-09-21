import os
from pydantic import model_validator
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Multilingual AI Voice Agent"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost/voiceagent"
    # Derived automatically from DATABASE_URL — asyncpg needs postgresql:// not postgresql+asyncpg://
    ASYNC_DATABASE_URL: str = ""
    DB_POOL_MAX_SIZE: int = 8
    DB_COMMAND_TIMEOUT_SECONDS: float = 15.0

    # Google Cloud
    GCP_PROJECT_ID: str = ""
    GCS_BUCKET_NAME: str = ""
    GOOGLE_APPLICATION_CREDENTIALS: str = ""

    # Firebase
    FIREBASE_PROJECT_ID: str = ""

    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    OPENAI_REALTIME_MODEL: str = "gpt-4o-realtime-preview"
    EMBEDDING_MODEL: str = "text-embedding-ada-002"

    # TEMPORARY testing switch: forces the realtime Companion to respond in English only.
    # Set env FORCE_ENGLISH_TEST_MODE=false to restore the existing multilingual behavior.
    FORCE_ENGLISH_TEST_MODE: bool = True

    # Long-session hardening (each independently reversible via env var).
    # Phase 1: bounded relay shutdown/cleanup + structured per-turn telemetry (no behavior change).
    SESSION_LIFECYCLE_FIX: bool = True
    TELEMETRY_ENABLED: bool = True
    # Phase 2: keep the model's history short and hand out the combo reason only on request.
    # off = existing behavior | allowlist = only uids in HISTORY_PRUNING_UIDS | all = every session
    HISTORY_PRUNING_MODE: str = "off"
    HISTORY_PRUNING_UIDS: str = ""      # comma-separated Firebase uids (allowlist mode)
    HISTORY_KEEP_TURNS: int = 3
    # Phase 3: response policy (stay_silent tool, no preamble, concise style). Same off/allowlist/all modes.
    RESPONSE_POLICY_MODE: str = "off"
    RESPONSE_POLICY_UIDS: str = ""      # comma-separated uids or 8+ char prefixes (allowlist mode)

    # Redis
    REDIS_URL: str = ""
    REDIS_LOOKUP_TTL_SECONDS: int = 3600    # 1 hour for exact combo lookups
    REDIS_SEMANTIC_TTL_SECONDS: int = 86400  # 24 hours for semantic search

    # SendGrid
    SENDGRID_API_KEY: str = ""
    SENDGRID_FROM_EMAIL: str = "tech@kriyora.com"

    @model_validator(mode="after")
    def derive_async_database_url(self) -> "Settings":
        if not self.ASYNC_DATABASE_URL:
            self.ASYNC_DATABASE_URL = self.DATABASE_URL.replace(
                "postgresql+asyncpg://", "postgresql://", 1
            )
        return self

    class Config:
        case_sensitive = True
        env_file = ".env"
        extra = "ignore"

settings = Settings()

# Securely bind Google credentials to the OS environment so all SDKs can find them
if settings.GOOGLE_APPLICATION_CREDENTIALS:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.GOOGLE_APPLICATION_CREDENTIALS
