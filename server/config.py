"""Server settings, read from environment variables and `.env`."""

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    auth_mode: Literal["none", "api_key", "oauth"] = "none"
    api_key: str = ""

    database_url: str = "postgresql+asyncpg://fitgen:fitgen@localhost:5434/fitgen"

    llm_timeout_seconds: float = 20.0
    llm_max_retries: int = 2

    queue_backend: Literal["local", "sqs"] = "local"
    # Background worker inside the server process (off in most tests).
    worker_enabled: bool = True
    worker_poll_seconds: float = 5.0
    # Regenerate the weekly insight this long after the last log_workout.
    insight_debounce_minutes: float = 10.0

    aws_region: str = "us-east-1"
    # Bedrock API key; empty uses the normal AWS credential chain (e.g. an EC2 role).
    bedrock_api_key: str = ""
    bedrock_model_id: str = "amazon.nova-lite-v1:0"
    sqs_queue_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
