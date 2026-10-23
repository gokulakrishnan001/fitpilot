"""Demo client backend: chat with the FitGen agent over SSE, and run widget button actions.

Run (from client/backend): uv run uvicorn fitgen_client.app:app --port 8100
"""

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from fitgen_client.agent import build_agent, stream_events
from fitgen_client.config import ClientSettings, get_settings
from fitgen_client.llm import build_chat_model
from fitgen_client.mcp_tools import call_tool, load_tools, mcp_session

logger = logging.getLogger("fitgen.client")


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    conversation_id: str | None = None


class ActionRequest(BaseModel):
    """A button in a tool result: call `tool` with `args`, no LLM round trip."""

    tool: str = Field(min_length=1, max_length=64)
    args: dict[str, Any] = Field(default_factory=dict)


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def create_app(settings: ClientSettings | None = None, model_factory=build_chat_model) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="FitGen demo client")

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/chat")
    async def chat(request: ChatRequest) -> StreamingResponse:
        conversation_id = request.conversation_id or str(uuid.uuid4())

        async def events() -> AsyncIterator[str]:
            try:
                model = model_factory(settings)
                async with mcp_session(settings.mcp_url, settings.mcp_api_key) as session:
                    agent = build_agent(model, await load_tools(session))
                    async for item in stream_events(agent, request.message, conversation_id):
                        yield _sse(item["event"], item["data"])
            except Exception:
                logger.exception("chat failed")
                yield _sse("error", {"message": "Sorry, something went wrong. Please try again."})

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/action")
    async def action(request: ActionRequest) -> dict[str, Any]:
        # Note: button actions don't go through the agent, so they aren't in its
        # conversation memory. Fine for the demo: FitGen's server holds the workout state.
        output = None
        try:
            async with mcp_session(settings.mcp_url, settings.mcp_api_key) as session:
                known = {t.name for t in (await session.list_tools()).tools}
                if request.tool in known:
                    output = await call_tool(session, request.tool, request.args)
        except Exception as exc:
            logger.exception("action %s failed", request.tool)
            raise HTTPException(status_code=502, detail="FitGen is unavailable") from exc
        # Raised outside the session: inside it, the transport's task group would wrap it.
        if output is None:
            raise HTTPException(status_code=404, detail=f"Unknown tool {request.tool!r}")
        return {"name": request.tool, "text": output.text, **output.as_artifact()}

    return app


app = create_app()
