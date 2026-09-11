import json
from dataclasses import dataclass

import boto3
import pytest
from moto import mock_aws

import transcribe_completion.handler as handler_module
from transcribe_completion.handler import handler

FILE_STATUS_TABLE = "test-file-status"
WORKER_QUEUE_NAME = "test-worker-queue"
UPLOADS_BUCKET = "test-uploads-bucket"
REGION = "us-east-1"
ACCOUNT_ID = "123456789012"

FILE_ID = "uploads/example.mp3"
JOB_NAME = "conduit-abc123"
ORIGINAL_AUDIO_SIZE = 5_000


@dataclass
class _FakeContext:
    invoked_function_arn: str = (
        f"arn:aws:lambda:{REGION}:{ACCOUNT_ID}:function:conduit-rag-dev-transcribe-completion"
    )


class _FakeTranscribeClient:
    """Stand-in for boto3's transcribe client -- moto's Transcribe coverage is
    uncertain, so the FAILED-job path patches this directly instead, same
    reasoning test_worker_handler.py already uses to patch embed_batch
    instead of relying on moto for Bedrock."""

    def __init__(self, failure_reason: str):
        self._failure_reason = failure_reason

    def get_transcription_job(self, TranscriptionJobName):  # noqa: N803 -- matches boto3's casing
        return {"TranscriptionJob": {"FailureReason": self._failure_reason}}


def _transcribe_result_json(words: list[tuple[str, float, float]]) -> dict:
    """Build a minimal Transcribe-shaped results.items payload: each word
    followed by a sentence-ending period, so parse_transcript_json_to_jsonl
    flushes one segment per word (real transcripts of course don't punctuate
    every word, but this keeps the fixture simple and deterministic)."""
    items = []
    for word, start, end in words:
        items.append(
            {
                "type": "pronunciation",
                "start_time": str(start),
                "end_time": str(end),
                "alternatives": [{"confidence": "1.0", "content": word}],
            }
        )
        items.append(
            {"type": "punctuation", "alternatives": [{"confidence": "1.0", "content": "."}]}
        )
    return {"results": {"items": items}}


@pytest.fixture
def aws_env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", REGION)
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name=REGION)
        dynamodb.create_table(
            TableName=FILE_STATUS_TABLE,
            KeySchema=[{"AttributeName": "file_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "file_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )

        sqs = boto3.client("sqs", region_name=REGION)
        worker_queue_url = sqs.create_queue(QueueName=WORKER_QUEUE_NAME)["QueueUrl"]

        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(Bucket=UPLOADS_BUCKET)
        s3.put_object(Bucket=UPLOADS_BUCKET, Key=FILE_ID, Body=b"x" * ORIGINAL_AUDIO_SIZE)

        monkeypatch.setattr(handler_module, "FILE_STATUS_TABLE", FILE_STATUS_TABLE)
        monkeypatch.setattr(handler_module, "WORKER_QUEUE_URL", worker_queue_url)
        monkeypatch.setattr(handler_module, "NUM_PARTITIONS", 10)
        # dispatcher tags the real job with (file_id, s3_bucket) at start time;
        # this test suite never starts a real job, so it stands in directly
        # rather than exercising list_tags_for_resource through moto.
        monkeypatch.setattr(
            handler_module, "recover_upload_identity", lambda job_arn: (FILE_ID, UPLOADS_BUCKET)
        )

        yield {
            "dynamodb": dynamodb,
            "sqs": sqs,
            "worker_queue_url": worker_queue_url,
            "s3": s3,
        }


def _event(status: str) -> dict:
    return {"detail": {"TranscriptionJobName": JOB_NAME, "TranscriptionJobStatus": status}}


def test_completed_job_stages_transcript_and_fans_out(aws_env):
    transcript_json = _transcribe_result_json([("Hello", 0.0, 0.5), ("world", 0.6, 1.0)])
    output_key = handler_module.transcribe_output_key(FILE_ID)
    aws_env["s3"].put_object(
        Bucket=UPLOADS_BUCKET, Key=output_key, Body=json.dumps(transcript_json).encode("utf-8")
    )

    handler(_event("COMPLETED"), _FakeContext())

    extracted_key = f"extracted/{FILE_ID}.extracted"
    extracted_body = (
        aws_env["s3"].get_object(Bucket=UPLOADS_BUCKET, Key=extracted_key)["Body"].read()
    )
    lines = [json.loads(line) for line in extracted_body.decode("utf-8").splitlines()]
    assert lines
    assert any("Hello" in line["text"] for line in lines)

    # file_status stays keyed on the ORIGINAL audio upload -- s3_key/
    # file_size_bytes must reflect it, not the staged JSONL transcript.
    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    item = table.get_item(Key={"file_id": FILE_ID})["Item"]
    assert item["status"] == "processing"
    assert item["s3_key"] == FILE_ID
    assert item["file_size_bytes"] == ORIGINAL_AUDIO_SIZE

    messages = _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"])
    assert len(messages) >= 1
    assert all(m["partition_format"] == "transcript" for m in messages)
    assert all(m["s3_key"] == extracted_key for m in messages)


def test_failed_job_marks_file_status_failed(aws_env, monkeypatch):
    monkeypatch.setattr(handler_module, "transcribe", _FakeTranscribeClient("audio unintelligible"))

    handler(_event("FAILED"), _FakeContext())

    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    item = table.get_item(Key={"file_id": FILE_ID})["Item"]
    assert item["status"] == "failed"
    assert item["error"] == "audio unintelligible"
    assert _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"]) == []


def test_completed_job_with_empty_transcript_marks_failed(aws_env):
    output_key = handler_module.transcribe_output_key(FILE_ID)
    aws_env["s3"].put_object(
        Bucket=UPLOADS_BUCKET,
        Key=output_key,
        Body=json.dumps({"results": {"items": []}}).encode("utf-8"),
    )

    handler(_event("COMPLETED"), _FakeContext())

    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    item = table.get_item(Key={"file_id": FILE_ID})["Item"]
    assert item["status"] == "failed"
    assert "empty transcript" in item["error"]
    assert _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"]) == []


def test_redelivered_completed_event_is_idempotent_noop(aws_env):
    transcript_json = _transcribe_result_json([("Hello", 0.0, 0.5), ("world", 0.6, 1.0)])
    output_key = handler_module.transcribe_output_key(FILE_ID)
    aws_env["s3"].put_object(
        Bucket=UPLOADS_BUCKET, Key=output_key, Body=json.dumps(transcript_json).encode("utf-8")
    )

    handler(_event("COMPLETED"), _FakeContext())
    _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"])  # drain first dispatch

    handler(_event("COMPLETED"), _FakeContext())  # redelivery of the same event
    assert _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"]) == []


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
