import json

import boto3
import pytest
from moto import mock_aws

import video_completion.handler as handler_module
from video_completion.handler import handler

FILE_STATUS_TABLE = "test-file-status"
WORKER_QUEUE_NAME = "test-worker-queue"
UPLOADS_BUCKET = "test-uploads-bucket"
REGION = "us-east-1"

FILE_ID = "uploads/example.mp4"
ORIGINAL_VIDEO_SIZE = 5_000


@pytest.fixture
def aws_env(monkeypatch):
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
        s3.put_object(Bucket=UPLOADS_BUCKET, Key=FILE_ID, Body=b"x" * ORIGINAL_VIDEO_SIZE)

        monkeypatch.setattr(handler_module, "FILE_STATUS_TABLE", FILE_STATUS_TABLE)
        monkeypatch.setattr(handler_module, "WORKER_QUEUE_URL", worker_queue_url)
        monkeypatch.setattr(handler_module, "NUM_PARTITIONS", 10)

        yield {
            "dynamodb": dynamodb,
            "sqs": sqs,
            "worker_queue_url": worker_queue_url,
            "s3": s3,
        }


def test_succeeded_execution_fans_out_as_video(aws_env):
    # Stands in for what the Fargate task would already have staged.
    detections = [
        {"start": float(i), "end": float(i) + 1.0, "text": f"Detected object #{i}"}
        for i in range(20)
    ]
    jsonl = ("\n".join(json.dumps(d) for d in detections) + "\n").encode("utf-8")
    extracted_key = f"extracted/{FILE_ID}.extracted"
    aws_env["s3"].put_object(Bucket=UPLOADS_BUCKET, Key=extracted_key, Body=jsonl)

    handler({"file_id": FILE_ID, "s3_bucket": UPLOADS_BUCKET, "status": "SUCCEEDED"}, None)

    # file_status stays keyed on the ORIGINAL video upload -- s3_key/
    # file_size_bytes must reflect it, not the staged JSONL detections.
    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    item = table.get_item(Key={"file_id": FILE_ID})["Item"]
    assert item["status"] == "processing"
    assert item["s3_key"] == FILE_ID
    assert item["file_size_bytes"] == ORIGINAL_VIDEO_SIZE

    messages = _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"])
    assert len(messages) >= 1
    assert all(m["partition_format"] == "video" for m in messages)
    assert all(m["s3_key"] == extracted_key for m in messages)


def test_failed_status_marks_file_status_failed(aws_env):
    handler(
        {
            "file_id": FILE_ID,
            "s3_bucket": UPLOADS_BUCKET,
            "status": "FAILED",
            "error": "task exited nonzero",
        },
        None,
    )

    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    item = table.get_item(Key={"file_id": FILE_ID})["Item"]
    assert item["status"] == "failed"
    assert item["error"] == "task exited nonzero"
    assert _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"]) == []


def test_redelivered_succeeded_payload_is_idempotent_noop(aws_env):
    detections = [{"start": 0.0, "end": 1.0, "text": "Detected object #0"}]
    jsonl = ("\n".join(json.dumps(d) for d in detections) + "\n").encode("utf-8")
    extracted_key = f"extracted/{FILE_ID}.extracted"
    aws_env["s3"].put_object(Bucket=UPLOADS_BUCKET, Key=extracted_key, Body=jsonl)

    handler({"file_id": FILE_ID, "s3_bucket": UPLOADS_BUCKET, "status": "SUCCEEDED"}, None)
    _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"])  # drain first dispatch

    handler({"file_id": FILE_ID, "s3_bucket": UPLOADS_BUCKET, "status": "SUCCEEDED"}, None)
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
