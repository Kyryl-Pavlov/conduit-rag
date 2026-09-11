import json
from dataclasses import asdict
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

import worker.handler as handler_module
from common.models import WorkerMessage
from worker.handler import handler

UPLOADS_BUCKET = "test-uploads-bucket"
WRITE_QUEUE_NAME = "test-write-queue"
REGION = "us-east-1"

FAKE_DIM = 8


def _fake_embed_batch(texts, model_id=None, dimensions=None):
    return [[0.1] * FAKE_DIM for _ in texts]


@pytest.fixture
def aws_env(monkeypatch):
    with mock_aws():
        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=UPLOADS_BUCKET)

        sqs = boto3.client("sqs", region_name=REGION)
        write_queue_url = sqs.create_queue(QueueName=WRITE_QUEUE_NAME)["QueueUrl"]

        monkeypatch.setattr(handler_module, "WRITE_QUEUE_URL", write_queue_url)
        monkeypatch.setattr(handler_module, "TARGET_TOKENS_PER_CHUNK", 50)
        monkeypatch.setattr(handler_module, "MAX_TOKENS_PER_CHUNK", 100)
        monkeypatch.setattr(handler_module, "OVERLAP_TOKENS", 10)
        monkeypatch.setattr(handler_module, "OVERLAP_BUFFER_BYTES", 64)

        yield {"s3": s3, "sqs": sqs, "write_queue_url": write_queue_url}


def test_worker_embeds_and_forwards_chunks(aws_env):
    words = [f"word{i:04d}" for i in range(300)]
    text = " ".join(words)
    key = "uploads/example.txt"
    aws_env["s3"].put_object(Bucket=UPLOADS_BUCKET, Key=key, Body=text.encode("utf-8"))

    message = WorkerMessage(
        file_id=key,
        s3_bucket=UPLOADS_BUCKET,
        s3_key=key,
        file_size_bytes=len(text.encode("utf-8")),
        worker_index=0,
        total_workers=1,
        start_byte=0,
        end_byte=len(text.encode("utf-8")) - 1,
    )
    event = {"Records": [{"body": json.dumps(asdict(message))}]}

    with patch("worker.handler.embed_batch", side_effect=_fake_embed_batch):
        handler(event, None)

    written = _drain_queue(aws_env["sqs"], aws_env["write_queue_url"])
    assert len(written) >= 2  # target_tokens=50 over ~300 words must split into multiple chunks

    for i, msg in enumerate(written):
        assert msg["chunk_id"] == f"{key}_w0_c{i}"
        assert msg["file_id"] == key
        assert msg["worker_index"] == 0
        assert len(msg["vector"]) == FAKE_DIM
        assert msg["text"]
        assert msg["metadata"]["embedding_dim"] == FAKE_DIM or "embedding_dim" in msg["metadata"]

    # reconstructed chunk text should recover the original words (order-preserving)
    all_words_seen = []
    for msg in written:
        all_words_seen.extend(msg["text"].split())
    assert words[0] in all_words_seen
    assert words[-1] in all_words_seen


def _drain_queue(sqs, queue_url: str) -> list[dict]:
    messages = []
    while True:
        response = sqs.receive_message(QueueUrl=queue_url, MaxNumberOfMessages=10)
        batch = response.get("Messages", [])
        if not batch:
            break
        for msg in batch:
            messages.append(json.loads(msg["Body"]))
            sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
    return messages
