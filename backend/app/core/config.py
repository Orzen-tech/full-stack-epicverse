import os
from pydantic_settings import BaseSettings
from pydantic import computed_field

class Settings(BaseSettings):
    PROJECT_NAME: str = "Multilingual AI Voice Agent"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost/voiceagent"
    DB_POOL_MIN_SIZE: int = 20
    DB_POOL_MAX_SIZE: int = 120
    DB_COMMAND_TIMEOUT_SECONDS: int = 5

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"
    REDIS_LOOKUP_TTL_SECONDS: int = 300
    REDIS_SEMANTIC_TTL_SECONDS: int = 120
    REDIS_SOCKET_TIMEOUT_SECONDS: float = 0.5

    # Google Cloud
    GCP_PROJECT_ID: str = ""
    GCS_BUCKET_NAME: str = ""
    GOOGLE_APPLICATION_CREDENTIALS: str = ""
    
    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini" # Using gpt-4o-mini as proxy for 4.1 mini
    EMBEDDING_MODEL: str = "text-embedding-3-small"
    REALTIME_MODEL: str = "gpt-4o-realtime-preview-2024-12-17"

    # Email Service (SendGrid)
    SENDGRID_API_KEY: str = ""
    SENDGRID_FROM_EMAIL: str = "noreply@epicverse.ai"

    @computed_field
    @property
    def ASYNC_DATABASE_URL(self) -> str:
        return self.DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://", 1)

    class Config:
        case_sensitive = True
        env_file = ".env"
        extra = "ignore"

settings = Settings()

# Securely bind Google credentials to the OS environment so all SDKs can find them
if settings.GOOGLE_APPLICATION_CREDENTIALS:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.GOOGLE_APPLICATION_CREDENTIALS
