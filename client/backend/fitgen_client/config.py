"""Demo client settings, from environment variables and `.env` (repo root or this folder)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[1]


class ClientSettings(BaseSettings):
    # Anchored to this file, not the working directory: the repo root's .env, then a
    # client-only .env next to pyproject.toml (later files win).
    model_config = SettingsConfigDict(
        env_file=(_BACKEND_DIR.parent.parent / ".env", _BACKEND_DIR / ".env"), extra="ignore"
    )

    mcp_url: str = "http://localhost:8000/mcp"
    # Sent as x-api-key when the FitGen server runs with AUTH_MODE=api_key.
    mcp_api_key: str = ""

    bedrock_chat_model_id: str = "amazon.nova-lite-v1:0"
    aws_region: str = "us-east-1"
    # Bedrock API key; empty uses the normal AWS credential chain (e.g. an EC2 role).
    bedrock_api_key: str = ""
    llm_timeout_seconds: float = 30.0
    llm_max_retries: int = 2


@lru_cache
def get_settings() -> ClientSettings:
    return ClientSettings()
