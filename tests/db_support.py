"""Shared helpers for tests that need Postgres and call tools over MCP."""

import os

import httpx2
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client

from server.config import get_settings
from server.db import session as db_session
from server.services.resolver_cache import clear_resolver_cache

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://fitgen:fitgen@localhost:5434/fitgen_test"
)
LATENCY_BUDGET_MS = 500


async def db_reachable() -> bool:
    engine = db_session.make_engine(TEST_DATABASE_URL)
    try:
        async with engine.connect():
            return True
    except Exception:
        return False
    finally:
        await engine.dispose()


def reset_db_caches() -> None:
    get_settings.cache_clear()
    db_session.get_engine.cache_clear()
    db_session.get_sessionmaker.cache_clear()
    clear_resolver_cache()


async def call(url: str, name: str, args: dict | None = None):
    async with httpx2.AsyncClient() as http:
        async with streamable_http_client(url, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await session.call_tool(name, args or {})


def text_of(result) -> str:
    return result.content[0].text


def assert_three_parts(result, display: str) -> None:
    assert result.content and result.content[0].type == "text"
    assert result.structured_content["display"] == display
    assert result.structured_content["components"]


class FakeLLM:
    """Stands in for Bedrock: returns canned replies in order, records the prompts."""

    model_id = "fake-model"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.prompts: list[str] = []

    async def complete(self, *, system: str, user: str, max_tokens: int = 600) -> str:
        self.prompts.append(user)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, Exception):
            raise reply
        return reply
