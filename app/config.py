"""Application settings, sourced entirely from environment variables."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLite file lives on a persistent volume in production (Railway: /app/data).
    database_url: str = "sqlite:///./data/patients.db"
    # Optional shared secret checked against the x-vapi-secret header.
    vapi_webhook_secret: str = ""
    log_level: str = "INFO"
    app_name: str = "Voice Patient Registration API"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
