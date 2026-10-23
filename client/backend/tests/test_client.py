"""Demo client backend against a real FitGen MCP server (scripted chat model)."""

import json

import pytest
from fastapi.testclient import TestClient
from langchain_aws import ChatBedrockConverse

from fitgen_client.agent import recent_turns
from fitgen_client.app import create_app
from fitgen_client.config import ClientSettings
from fitgen_client.llm import build_chat_model
from fitgen_client.mcp_tools import load_tools, mcp_session
from tests.conftest import API_KEY
from tests.fakes import ScriptedChatModel, tool_call


def settings(url: str, **overrides) -> ClientSettings:
    # _env_file=None: tests don't depend on the developer's .env.
    return ClientSettings(
        _env_file=None, mcp_url=url, mcp_api_key=API_KEY, **overrides
    )


def parse_sse(body: str) -> list[tuple[str, dict]]:
    events = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def chat(client: TestClient, message: str, conversation_id: str | None = None):
    response = client.post(
        "/api/chat", json={"message": message, "conversation_id": conversation_id}
    )
    assert response.status_code == 200
    return parse_sse(response.text)


# --- MCP tool wrapping ----------------------------------------------------------------------


async def test_tools_load_with_schemas(fitgen_url):
    async with mcp_session(fitgen_url, API_KEY) as session:
        tools = {t.name: t for t in await load_tools(session)}
        assert "ping" not in tools
        assert {"log_workout", "start_session", "complete_set", "plate_calculator"} <= set(tools)
        assert tools["log_workout"].args_schema["properties"]["sets"]["minimum"] == 1

        message = await tools["plate_calculator"].ainvoke(
            {"type": "tool_call", "id": "c1", "name": "plate_calculator", "args": {"weight": 225}}
        )
    assert message.content == "For 225 pounds, put two 45s on each side of the 45-pound bar."
    assert message.artifact["components"][0]["type"] == "plate_diagram"
    assert message.artifact == message.artifact | {"display": "fragment", "is_error": False}


async def test_wrong_api_key_is_rejected(fitgen_url):
    with pytest.raises(Exception):  # noqa: B017 - the transport surfaces the 401 its own way
        async with mcp_session(fitgen_url, "wrong-key") as session:
            await session.list_tools()


# --- /api/chat ------------------------------------------------------------------------------


def test_chat_streams_tool_result_and_reply(fitgen_url):
    model = ScriptedChatModel(
        replies=[tool_call("plate_calculator", {"weight": 225}), _ai("Two 45s on each side.")]
    )
    client = TestClient(create_app(settings(fitgen_url), model_factory=lambda s: model))
    events = chat(client, "What plates for 225?")
    kinds = [e for e, _ in events]

    assert kinds[0] == "tool_start" and events[0][1]["name"] == "plate_calculator"
    result = next(d for e, d in events if e == "tool_result")
    assert result["name"] == "plate_calculator"
    assert result["text"].startswith("For 225 pounds")
    assert result["components"][0]["type"] == "plate_diagram"
    done = events[-1]
    # Tools return directly: FitGen's own text is the spoken reply, in one model call.
    assert done[0] == "done" and done[1]["text"] == result["text"]
    assert len(model.seen) == 1


def test_chat_remembers_the_conversation(fitgen_url):
    model = ScriptedChatModel(replies=[_ai("Hi there."), _ai("You said hello.")])
    client = TestClient(create_app(settings(fitgen_url), model_factory=lambda s: model))
    first = chat(client, "hello")
    conversation_id = first[-1][1]["conversation_id"]
    assert first[-1] == ("done", {"conversation_id": conversation_id, "text": "Hi there."})
    chat(client, "what did I say?", conversation_id)
    history = [m.content for m in model.seen[1]]
    assert "hello" in history and "Hi there." in history


def test_tool_errors_reach_the_model_and_the_screen(fitgen_url):
    model = ScriptedChatModel(
        replies=[tool_call("get_form_cues", {"exercise": "zumba"}), _ai("Which exercise?")]
    )
    client = TestClient(create_app(settings(fitgen_url), model_factory=lambda s: model))
    result = next(d for e, d in chat(client, "how do I zumba") if e == "tool_result")
    assert result["is_error"] and "couldn't find" in result["text"]
    assert result["components"][0]["type"] == "alert"


# --- /api/action ----------------------------------------------------------------------------


def test_action_calls_the_tool_directly(fitgen_url):
    client = TestClient(create_app(settings(fitgen_url), model_factory=_no_model))
    response = client.post(
        "/api/action", json={"tool": "plate_calculator", "args": {"weight": 135}}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "For 135 pounds, put a 45 on each side of the 45-pound bar."
    assert body["components"][0]["type"] == "plate_diagram"

    unknown = client.post("/api/action", json={"tool": "drop_tables", "args": {}})
    assert unknown.status_code == 404


def test_session_buttons_drive_a_workout(fitgen_url):
    client = TestClient(create_app(settings(fitgen_url), model_factory=_no_model))
    start = client.post("/api/action", json={"tool": "start_session"}).json()
    buttons = [
        b["action"]
        for c in start["components"]
        if c["type"] == "layout"
        for b in c["children"]
        if b["type"] == "button"
    ]
    assert buttons == ["complete_set", "skip_exercise", "end_session"]
    done = client.post("/api/action", json={"tool": "complete_set"}).json()
    assert done["text"].startswith("Set 1 of ")
    client.post("/api/action", json={"tool": "end_session"})


# --- model factory --------------------------------------------------------------------------


def test_build_chat_model():
    bedrock = build_chat_model(ClientSettings(_env_file=None))
    assert isinstance(bedrock, ChatBedrockConverse)
    assert bedrock.model_id == "amazon.nova-lite-v1:0"
    keyed = build_chat_model(
        ClientSettings(_env_file=None, bedrock_api_key="abc")
    )
    assert keyed.bedrock_api_key.get_secret_value() == "abc"


def _ai(text: str):
    from langchain_core.messages import AIMessage

    return AIMessage(content=text)


def _no_model(settings):
    raise AssertionError("button actions must not call the LLM")


def test_failed_tool_call_is_answered_and_conversation_continues(fitgen_url, monkeypatch):
    import fitgen_client.mcp_tools as mcp_tools

    real = mcp_tools.call_tool
    calls = {"n": 0}

    async def flaky(session, name, arguments):
        calls["n"] += 1
        if calls["n"] == 1:
            raise ConnectionError("server restarted")
        return await real(session, name, arguments)

    monkeypatch.setattr(mcp_tools, "call_tool", flaky)
    model = ScriptedChatModel(
        replies=[
            tool_call("plate_calculator", {"weight": 225}, "call-1"),
            tool_call("plate_calculator", {"weight": 135}, "call-2"),
        ]
    )
    client = TestClient(create_app(settings(fitgen_url), model_factory=lambda s: model))
    first = chat(client, "plates for 225")
    assert first[-1][1]["text"] == mcp_tools.UNREACHABLE
    second = chat(client, "plates for 135", first[-1][1]["conversation_id"])
    assert second[-1][1]["text"].startswith("For 135 pounds")
    # The failed call was answered in memory, so the history stays valid.
    sent = model.seen[1]
    assert any(getattr(m, "tool_call_id", None) == "call-1" for m in sent)


def test_sdk_argument_errors_are_not_read_aloud(fitgen_url):
    model = ScriptedChatModel(replies=[tool_call("plate_calculator", {"weight": "abc"})])
    client = TestClient(create_app(settings(fitgen_url), model_factory=lambda s: model))
    events = chat(client, "plates for abc")
    assert events[-1][1]["text"] == "Sorry, I didn't catch that. Could you say it again?"
    assert "pydantic" not in json.dumps(events)


# --- every turn goes through a tool ---------------------------------------------------------


def test_model_must_call_a_tool_and_sees_recent_turns_only(fitgen_url):
    replies = [tool_call("reply", {"text": f"Reply {i}."}, f"r{i}") for i in range(5)]
    model = ScriptedChatModel(replies=replies)
    client = TestClient(create_app(settings(fitgen_url), model_factory=lambda s: model))
    conversation_id = None
    for i in range(5):
        events = chat(client, f"message {i}", conversation_id)
        conversation_id = events[-1][1]["conversation_id"]
        assert events[-1][1]["text"] == f"Reply {i}."  # the reply tool's text is spoken

    assert all(k.get("tool_choice") == "any" for k in model.bind_kwargs)
    assert "reply" in model.bind_kwargs[0]["tools"]
    last_request = model.seen[-1]
    users = [m.content for m in last_request if m.type == "human"]
    assert users == ["message 2", "message 3", "message 4"]  # only the last 3 turns


def test_recent_turns_never_splits_a_tool_call():
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

    history = []
    for i in range(5):
        history += [
            HumanMessage(f"u{i}"),
            AIMessage("", tool_calls=[{"name": "t", "args": {}, "id": f"c{i}"}]),
            ToolMessage("ok", tool_call_id=f"c{i}"),
        ]
    trimmed = recent_turns(history, keep=2)
    assert trimmed[0].content == "u3" and len(trimmed) == 6
    assert recent_turns(history[:3], keep=2) == history[:3]
