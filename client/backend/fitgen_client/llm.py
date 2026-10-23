"""Chat model for the demo agent: Amazon Bedrock (Converse API)."""

from langchain_aws import ChatBedrockConverse
from langchain_core.language_models.chat_models import BaseChatModel

from fitgen_client.config import ClientSettings


def build_chat_model(settings: ClientSettings) -> BaseChatModel:
    # TODO(Phase 9): also limit Bedrock to one tool call per turn once tested live.
    return ChatBedrockConverse(
        model=settings.bedrock_chat_model_id,
        region_name=settings.aws_region,
        api_key=settings.bedrock_api_key or None,
        temperature=0.3,
    )
