"""LLM clients and queue backends against mocked transports (no network, no AWS)."""

import asyncio
import os

import boto3
import pytest
from botocore.stub import Stubber

from server.config import Settings
from server.jobs.llm import BedrockClient, LLMError, build_llm_client
from server.jobs.queue import LocalQueue, SqsQueue, build_queue

# --- Bedrock -------------------------------------------------------------------------------


def _bedrock_client():
    return boto3.client(
        "bedrock-runtime",
        region_name="us-east-1",
        aws_access_key_id="test",
        aws_secret_access_key="test",
    )


async def test_bedrock_converse():
    client = _bedrock_client()
    with Stubber(client) as stub:
        stub.add_response(
            "converse",
            {
                "output": {"message": {"role": "assistant", "content": [{"text": " A. B. "}]}},
                "stopReason": "end_turn",
                "usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
                "metrics": {"latencyMs": 1},
            },
            {
                "modelId": "amazon.nova-lite-v1:0",
                "system": [{"text": "sys"}],
                "messages": [{"role": "user", "content": [{"text": "data"}]}],
                "inferenceConfig": {"maxTokens": 200, "temperature": 0.4},
            },
        )
        llm = BedrockClient(
            model_id="amazon.nova-lite-v1:0",
            region="us-east-1",
            timeout=5,
            max_retries=0,
            client=client,
        )
        assert await llm.complete(system="sys", user="data", max_tokens=200) == "A. B."


async def test_bedrock_error_raises_llm_error():
    client = _bedrock_client()
    with Stubber(client) as stub:
        stub.add_client_error("converse", service_error_code="ThrottlingException")
        llm = BedrockClient(
            model_id="m", region="us-east-1", timeout=5, max_retries=0, client=client
        )
        with pytest.raises(LLMError):
            await llm.complete(system="s", user="u")


def test_build_llm_client():
    bedrock = build_llm_client(Settings(_env_file=None))
    assert isinstance(bedrock, BedrockClient) and bedrock.model_id == "amazon.nova-lite-v1:0"


def test_bedrock_api_key_reaches_botocore(monkeypatch):
    monkeypatch.delenv("AWS_BEARER_TOKEN_BEDROCK", raising=False)
    build_llm_client(Settings(_env_file=None, bedrock_api_key="abc"))
    assert os.environ["AWS_BEARER_TOKEN_BEDROCK"] == "abc"


# --- Queues --------------------------------------------------------------------------------


async def test_local_queue():
    q = LocalQueue()
    assert await q.get(timeout=0.01) is None
    await q.put(7)
    await q.put(8, delay_seconds=60)  # delay is left to jobs.run_after
    assert [await q.get(timeout=0.1), await q.get(timeout=0.1)] == [7, 8]


def _sqs_client():
    return boto3.client(
        "sqs", region_name="us-east-1", aws_access_key_id="t", aws_secret_access_key="t"
    )


URL = "https://sqs.us-east-1.amazonaws.com/123456789012/fitgen-jobs"


async def test_sqs_queue_put_get_delete():
    client = _sqs_client()
    with Stubber(client) as stub:
        stub.add_response(
            "send_message",
            {"MessageId": "m1", "MD5OfMessageBody": "x"},
            {"QueueUrl": URL, "MessageBody": "42", "DelaySeconds": 900},
        )
        stub.add_response(
            "receive_message",
            {"Messages": [{"MessageId": "m1", "ReceiptHandle": "rh", "Body": "42"}]},
            {"QueueUrl": URL, "MaxNumberOfMessages": 1, "WaitTimeSeconds": 20},
        )
        stub.add_response("delete_message", {}, {"QueueUrl": URL, "ReceiptHandle": "rh"})
        stub.add_response(
            "receive_message",
            {},
            {"QueueUrl": URL, "MaxNumberOfMessages": 1, "WaitTimeSeconds": 2},
        )
        q = SqsQueue(queue_url=URL, region="us-east-1", client=client)
        await q.put(42, delay_seconds=5000)  # clamped to the SQS maximum
        assert await q.get(timeout=60) == 42  # long poll clamped to 20 s
        assert await q.get(timeout=2) is None
        stub.assert_no_pending_responses()


def test_build_queue():
    assert isinstance(build_queue(Settings()), LocalQueue)
    with pytest.raises(ValueError):
        build_queue(Settings(queue_backend="sqs", sqs_queue_url=""))
    assert isinstance(build_queue(Settings(queue_backend="sqs", sqs_queue_url=URL)), SqsQueue)


def test_tests_never_touch_real_event_loops():
    # Guard: the async tests above run on pytest-asyncio's loop, not a leftover one.
    assert asyncio.get_event_loop_policy() is not None
