"""LLM clients for background jobs. Never called on the request path.

Amazon Bedrock (Converse API), with a per-call timeout and retries for transient failures.
"""

import asyncio
import os
from typing import Protocol

from server.config import Settings


class LLMError(RuntimeError):
    """The model call failed after retries, or returned nothing usable."""


class LLMClient(Protocol):
    model_id: str

    async def complete(self, *, system: str, user: str, max_tokens: int = 600) -> str:
        """One system + user turn; returns the model's text."""
        ...


class BedrockClient:
    def __init__(
        self,
        *,
        model_id: str,
        region: str,
        timeout: float,
        max_retries: int,
        api_key: str = "",
        client=None,
    ):
        self.model_id = model_id
        if api_key:
            # botocore reads Bedrock API keys only from this variable.
            os.environ["AWS_BEARER_TOKEN_BEDROCK"] = api_key
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "bedrock-runtime",
                region_name=region,
                config=Config(
                    connect_timeout=min(timeout, 5.0),
                    read_timeout=timeout,
                    retries={"max_attempts": max_retries + 1, "mode": "standard"},
                ),
            )
        self._client = client

    async def complete(self, *, system: str, user: str, max_tokens: int = 600) -> str:
        from botocore.exceptions import BotoCoreError, ClientError

        def call() -> dict:
            return self._client.converse(
                modelId=self.model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": [{"text": user}]}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0.4},
            )

        try:
            response = await asyncio.to_thread(call)  # boto3 is synchronous
        except (BotoCoreError, ClientError) as exc:
            raise LLMError(f"Bedrock call failed: {exc}") from exc
        blocks = response.get("output", {}).get("message", {}).get("content", [])
        text = "".join(b.get("text", "") for b in blocks).strip()
        if not text:
            raise LLMError("Bedrock returned an empty response")
        return text


def build_llm_client(settings: Settings) -> LLMClient:
    return BedrockClient(
        model_id=settings.bedrock_model_id,
        region=settings.aws_region,
        timeout=settings.llm_timeout_seconds,
        max_retries=settings.llm_max_retries,
        api_key=settings.bedrock_api_key,
    )
