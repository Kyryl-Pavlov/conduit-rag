import json
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

import dispatcher.handler as handler_module
from dispatcher.handler import handler

FILE_STATUS_TABLE = "test-file-status"
WORKER_QUEUE_NAME = "test-worker-queue"
UPLOADS_BUCKET = "test-uploads-bucket"
REGION = "us-east-1"


def _build_minimal_pdf(text: str) -> bytes:
    """Hand-build a minimal valid single-page PDF containing `text` as a
    single Tj show-text operator -- no reportlab/pypdf.PdfWriter needed (the
    latter can't draw text), just enough of the PDF object model for pypdf's
    extract_text() to recover it. `text=""` produces a page with no content
    stream text at all, for exercising the empty-extraction failure path."""
    stream = f"BT /F1 24 Tf 72 100 Td ({text}) Tj ET".encode() if text else b""
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> "
        b"/MediaBox [0 0 200 200] /Contents 5 0 R >>",
        4: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        5: b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    }
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for obj_id in sorted(objects):
        offsets[obj_id] = len(out)
        out += f"{obj_id} 0 obj\n".encode() + objects[obj_id] + b"\nendobj\n"
    xref_offset = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for obj_id in range(1, len(objects) + 1):
        out += f"{offsets[obj_id]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n".encode()
    out += f"startxref\n{xref_offset}\n%%EOF".encode()
    return bytes(out)


def _s3_event_sqs_record(bucket: str, key: str, size: int) -> dict:
    s3_event = {
        "Records": [
            {
                "eventName": "ObjectCreated:Put",
                "s3": {
                    "bucket": {"name": bucket},
                    "object": {"key": key, "size": size},
                },
            }
        ]
    }
    return {"body": json.dumps(s3_event)}


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

        monkeypatch.setattr(handler_module, "FILE_STATUS_TABLE", FILE_STATUS_TABLE)
        monkeypatch.setattr(handler_module, "WORKER_QUEUE_URL", worker_queue_url)
        monkeypatch.setattr(handler_module, "NUM_PARTITIONS", 10)

        yield {
            "dynamodb": dynamodb,
            "sqs": sqs,
            "worker_queue_url": worker_queue_url,
            "s3": s3,
        }


def test_dispatch_creates_file_status_and_fans_out_worker_messages(aws_env):
    event = {"Records": [_s3_event_sqs_record("uploads-bucket", "uploads/example.txt", 10_000)]}

    handler(event, None)

    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    item = table.get_item(Key={"file_id": "uploads/example.txt"})["Item"]
    assert item["status"] == "processing"
    assert item["total_workers"] == 10
    assert item["completed_workers"] == 0
    assert item["file_size_bytes"] == 10_000

    messages = _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"])
    assert len(messages) == 10
    worker_indices = sorted(m["worker_index"] for m in messages)
    assert worker_indices == list(range(10))
    assert all(m["file_id"] == "uploads/example.txt" for m in messages)
    assert all(m["total_workers"] == 10 for m in messages)


def test_small_file_gets_fewer_than_ten_partitions(aws_env):
    event = {"Records": [_s3_event_sqs_record("uploads-bucket", "uploads/tiny.txt", 3)]}

    handler(event, None)

    messages = _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"])
    assert len(messages) == 3


def test_redelivered_event_is_idempotent_noop(aws_env):
    event = {"Records": [_s3_event_sqs_record("uploads-bucket", "uploads/example.txt", 10_000)]}

    handler(event, None)
    _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"])  # drain first dispatch

    handler(event, None)  # redelivery of the same event
    messages = _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"])
    assert messages == []


def test_pdf_upload_stages_extracted_text_and_fans_out_partitions(aws_env):
    pdf_bytes = _build_minimal_pdf("Hello World")
    key = "uploads/example.pdf"
    aws_env["s3"].put_object(Bucket=UPLOADS_BUCKET, Key=key, Body=pdf_bytes)
    event = {"Records": [_s3_event_sqs_record(UPLOADS_BUCKET, key, len(pdf_bytes))]}

    handler(event, None)

    extracted_key = f"extracted/{key}.extracted"
    extracted_body = (
        aws_env["s3"].get_object(Bucket=UPLOADS_BUCKET, Key=extracted_key)["Body"].read()
    )
    assert "Hello World" in extracted_body.decode("utf-8")

    # file_status stays keyed on the ORIGINAL pdf -- s3_key/file_size_bytes
    # must reflect the upload, not the extracted-text stand-in, since the
    # frontend displays this as the file's size.
    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    item = table.get_item(Key={"file_id": key})["Item"]
    assert item["s3_key"] == key
    assert item["file_size_bytes"] == len(pdf_bytes)

    messages = _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"])
    assert len(messages) >= 1
    assert all(m["file_id"] == key for m in messages)
    assert all(m["s3_key"] == extracted_key for m in messages)
    assert all(m["file_size_bytes"] == len(extracted_body) for m in messages)


def test_pdf_with_no_extractable_text_is_marked_failed(aws_env):
    pdf_bytes = _build_minimal_pdf("")
    key = "uploads/blank.pdf"
    aws_env["s3"].put_object(Bucket=UPLOADS_BUCKET, Key=key, Body=pdf_bytes)
    event = {"Records": [_s3_event_sqs_record(UPLOADS_BUCKET, key, len(pdf_bytes))]}

    handler(event, None)  # must not raise -- SQS would retry forever otherwise

    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    item = table.get_item(Key={"file_id": key})["Item"]
    assert item["status"] == "failed"
    assert "no extractable text" in item["error"]
    assert _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"]) == []


def test_unsupported_extension_is_marked_failed(aws_env):
    key = "uploads/example.docx"
    event = {"Records": [_s3_event_sqs_record(UPLOADS_BUCKET, key, 100)]}

    handler(event, None)  # must not raise -- SQS would retry forever otherwise

    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    item = table.get_item(Key={"file_id": key})["Item"]
    assert item["status"] == "failed"
    assert "unsupported file extension" in item["error"]
    assert _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"]) == []


def test_empty_file_is_marked_failed(aws_env):
    key = "uploads/empty.txt"
    event = {"Records": [_s3_event_sqs_record(UPLOADS_BUCKET, key, 0)]}

    handler(event, None)  # must not raise -- SQS would retry forever otherwise

    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    item = table.get_item(Key={"file_id": key})["Item"]
    assert item["status"] == "failed"
    assert "empty file" in item["error"]
    assert _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"]) == []


def test_audio_upload_fake_mode_fans_out_as_transcript(aws_env, monkeypatch):
    monkeypatch.setattr(handler_module, "TRANSCRIPTION_PROVIDER", "fake")
    key = "uploads/example.mp3"
    event = {"Records": [_s3_event_sqs_record(UPLOADS_BUCKET, key, 5_000)]}

    handler(event, None)

    extracted_key = f"extracted/{key}.extracted"
    extracted_body = (
        aws_env["s3"].get_object(Bucket=UPLOADS_BUCKET, Key=extracted_key)["Body"].read()
    )
    assert extracted_body  # fake_transcript_jsonl fabricated something

    # file_status stays keyed on the ORIGINAL audio upload -- s3_key/
    # file_size_bytes must reflect it, not the staged JSONL transcript, same
    # split the PDF path already makes.
    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    item = table.get_item(Key={"file_id": key})["Item"]
    assert item["s3_key"] == key
    assert item["file_size_bytes"] == 5_000

    messages = _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"])
    assert len(messages) >= 1
    assert all(m["file_id"] == key for m in messages)
    assert all(m["s3_key"] == extracted_key for m in messages)
    assert all(m["partition_format"] == "transcript" for m in messages)


def test_audio_upload_real_mode_starts_job_without_file_status(aws_env, monkeypatch):
    monkeypatch.setattr(handler_module, "TRANSCRIPTION_PROVIDER", "transcribe")
    key = "uploads/example.mp3"
    event = {"Records": [_s3_event_sqs_record(UPLOADS_BUCKET, key, 5_000)]}

    with patch("dispatcher.handler.start_transcription_job") as mock_start:
        handler(event, None)

    mock_start.assert_called_once_with(UPLOADS_BUCKET, key)

    # No file_status record yet -- transcription is async, and
    # transcribe_completion (not dispatcher) creates it once the job finishes.
    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    assert "Item" not in table.get_item(Key={"file_id": key})
    assert _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"]) == []


def test_oversized_audio_is_marked_failed(aws_env, monkeypatch):
    monkeypatch.setattr(handler_module, "MAX_AUDIO_BYTES", 1_000)
    key = "uploads/huge.mp3"
    event = {"Records": [_s3_event_sqs_record(UPLOADS_BUCKET, key, 2_000)]}

    handler(event, None)  # must not raise -- SQS would retry forever otherwise

    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    item = table.get_item(Key={"file_id": key})["Item"]
    assert item["status"] == "failed"
    assert "MAX_AUDIO_BYTES" in item["error"]
    assert _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"]) == []


def test_empty_audio_is_marked_failed(aws_env):
    key = "uploads/empty.mp3"
    event = {"Records": [_s3_event_sqs_record(UPLOADS_BUCKET, key, 0)]}

    handler(event, None)  # must not raise -- SQS would retry forever otherwise

    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    item = table.get_item(Key={"file_id": key})["Item"]
    assert item["status"] == "failed"
    assert "empty file" in item["error"]
    assert _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"]) == []


def test_video_upload_fake_mode_fans_out_as_video(aws_env, monkeypatch):
    monkeypatch.setattr(handler_module, "VIDEO_PROVIDER", "fake")
    key = "uploads/example.mp4"
    event = {"Records": [_s3_event_sqs_record(UPLOADS_BUCKET, key, 5_000)]}

    handler(event, None)

    extracted_key = f"extracted/{key}.extracted"
    extracted_body = (
        aws_env["s3"].get_object(Bucket=UPLOADS_BUCKET, Key=extracted_key)["Body"].read()
    )
    assert extracted_body  # fake_video_objects_jsonl fabricated something

    # file_status stays keyed on the ORIGINAL video upload -- s3_key/
    # file_size_bytes must reflect it, not the staged JSONL detections, same
    # split the PDF/audio paths already make.
    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    item = table.get_item(Key={"file_id": key})["Item"]
    assert item["s3_key"] == key
    assert item["file_size_bytes"] == 5_000

    messages = _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"])
    assert len(messages) >= 1
    assert all(m["file_id"] == key for m in messages)
    assert all(m["s3_key"] == extracted_key for m in messages)
    assert all(m["partition_format"] == "video" for m in messages)


def test_video_upload_real_mode_starts_execution_without_file_status(aws_env, monkeypatch):
    monkeypatch.setattr(handler_module, "VIDEO_PROVIDER", "some-real-provider")
    monkeypatch.setattr(
        handler_module,
        "VIDEO_STATE_MACHINE_ARN",
        "arn:aws:states:us-east-1:123456789012:stateMachine:x",
    )
    key = "uploads/example.mp4"
    event = {"Records": [_s3_event_sqs_record(UPLOADS_BUCKET, key, 5_000)]}

    with patch("dispatcher.handler.start_video_analysis_execution") as mock_start:
        handler(event, None)

    mock_start.assert_called_once_with(
        "arn:aws:states:us-east-1:123456789012:stateMachine:x", UPLOADS_BUCKET, key
    )

    # No file_status record yet -- video analysis is async, and
    # video_completion (not dispatcher) creates it once the execution finishes.
    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    assert "Item" not in table.get_item(Key={"file_id": key})
    assert _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"]) == []


def test_oversized_video_is_marked_failed(aws_env, monkeypatch):
    monkeypatch.setattr(handler_module, "MAX_VIDEO_BYTES", 1_000)
    key = "uploads/huge.mp4"
    event = {"Records": [_s3_event_sqs_record(UPLOADS_BUCKET, key, 2_000)]}

    handler(event, None)  # must not raise -- SQS would retry forever otherwise

    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    item = table.get_item(Key={"file_id": key})["Item"]
    assert item["status"] == "failed"
    assert "MAX_VIDEO_BYTES" in item["error"]
    assert _drain_queue(aws_env["sqs"], aws_env["worker_queue_url"]) == []


def test_empty_video_is_marked_failed(aws_env):
    key = "uploads/empty.mp4"
    event = {"Records": [_s3_event_sqs_record(UPLOADS_BUCKET, key, 0)]}

    handler(event, None)  # must not raise -- SQS would retry forever otherwise

    table = aws_env["dynamodb"].Table(FILE_STATUS_TABLE)
    item = table.get_item(Key={"file_id": key})["Item"]
    assert item["status"] == "failed"
    assert "empty file" in item["error"]
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
