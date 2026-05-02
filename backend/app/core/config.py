import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    PROJECT_NAME: str = "Multilingual AI Voice Agent"
    VERSION: str = "1.0.0"
    API_V1_STR: str = "/api/v1"
    
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@localhost/voiceagent"
    
    # Google Cloud
    GCP_PROJECT_ID: str = ""
    GCS_BUCKET_NAME: str = ""
    GOOGLE_APPLICATION_CREDENTIALS: str = ""
    
    # OpenAI
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4o-mini"
    # Realtime voice model. Default is the undated alias so we automatically
    # track the latest supported preview and don't break when OpenAI retires a
    # specific dated snapshot. Override via env (OPENAI_REALTIME_MODEL) to pin
    # to a specific snapshot like "gpt-4o-realtime-preview-2024-12-17".
    OPENAI_REALTIME_MODEL: str = "gpt-4o-realtime-preview"

    # SendGrid
    SENDGRID_API_KEY: str = ""
    SENDGRID_FROM_EMAIL: str = "tech@kriyora.com"

    class Config:
        case_sensitive = True
        env_file = ".env"
        extra = "ignore"

settings = Settings()

# Securely bind Google credentials to the OS environment so all SDKs can find them
if settings.GOOGLE_APPLICATION_CREDENTIALS:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = settings.GOOGLE_APPLICATION_CREDENTIALS
