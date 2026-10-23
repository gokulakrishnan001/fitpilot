"""Opt-in: the real LLM (Bedrock) driving the real FitGen server. Spends quota.

Run with: FITGEN_LIVE_LLM=1 uv run pytest tests/test_live.py -s
"""

import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from fitgen_client.app import create_app
from fitgen_client.config import ClientSettings
from tests.conftest import API_KEY

pytestmark = pytest.mark.skipif(
    os.environ.get("FITGEN_LIVE_LLM") != "1", reason="set FITGEN_LIVE_LLM=1 to call the real LLM"
)

# What the user says → the tool (and arguments) the agent should call.
CONVERSATION = [
    ("What's my workout today?", "get_todays_workout", {}),
    ("Start my workout", "start_session", {}),
    ("Done, that felt easy", "complete_set", {"effort": "easy"}),
    ("Done", "complete_set", {}),
    ("Only got 4 on that one", "complete_set", {"reps": 4}),
    ("Undo", "undo_last_log", {}),
    ("Done", "complete_set", {}),
    ("Skip this", "skip_exercise", {}),
    ("Done", "complete_set", {}),
    ("End workout", "end_session", {}),
    ("I weigh 184.6 pounds today", "log_body_metric", {"bodyweight": 184.6}),
    # Vague: either FitGen's cues tool asks "did you mean…", or the agent asks via reply.
    ("How do I press?", ("get_form_cues", "reply"), {}),
    ("Hello!", "reply", {}),
    ("What plates do I need for 225 pounds?", "plate_calculator", {"weight": 225}),
]


def _events(body: str) -> list[tuple[str, dict]]:
    out = []
    for block in body.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.splitlines())
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def test_agent_picks_the_right_tools(fitgen_url):
    settings = ClientSettings(mcp_url=fitgen_url, mcp_api_key=API_KEY)
    client = TestClient(create_app(settings))
    conversation_id = None
    for said, expected_tool, expected_args in CONVERSATION:
        time.sleep(4)  # people take longer than the 3 s retry window between sets
        start = time.perf_counter()
        response = client.post(
            "/api/chat", json={"message": said, "conversation_id": conversation_id}
        )
        seconds = time.perf_counter() - start
        events = _events(response.text)
        assert events[-1][0] == "done", events
        conversation_id = events[-1][1]["conversation_id"]
        calls = [d for e, d in events if e == "tool_start"]
        print(f"\nUSER: {said}\n  tools: {[(c['name'], c['args']) for c in calls]}")
        print(f"  FITGEN ({seconds:.1f}s): {events[-1][1]['text']}")
        assert calls, f"no tool call for {said!r}"
        assert len(calls) == 1, "one tool per turn"
        allowed = expected_tool if isinstance(expected_tool, tuple) else (expected_tool,)
        assert calls[0]["name"] in allowed
        for key, value in expected_args.items():
            assert calls[0]["args"].get(key) == value, calls[0]["args"]
        assert events[-1][1]["text"]
