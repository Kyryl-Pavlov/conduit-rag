"""Dispatcher Lambda: partitions a newly-uploaded file and fans out worker-queue messages.

Triggered by the dispatch-queue (SQS), whose messages are raw S3 `ObjectCreated`
event notifications (S3 -> SQS delivers the event body unwrapped -- no extra
envelope). File size is read directly from the event's `object.size`, so for
.txt files this Lambda never calls `HeadObject`. .pdf files are the exception:
there's no way to know how much text a PDF contains without parsing it, so the
dispatcher downloads it, extracts its text via `common.pdf.extract_pdf_text`,
and re-uploads the extracted plain text to `extracted/{file_id}.extracted` in
the same bucket -- `.extracted`, never `.txt`/`.pdf`, since either of those
would re-trigger this same S3 notification (see infra/cloud/main.tf) and
double-dispatch the extracted object as if it were an independent upload.

Partitioning and worker fan-out then run identically for both file types,
against whichever object (original .txt, or extracted-text stand-in for .pdf)
holds plain text: `WorkerMessage.s3_key`/`file_size_bytes` point at that text
object, while `file_id` (and the `file_status` record's `s3_key`/
`file_size_bytes`, used for status display) always stay the original upload's
key/size. worker/db_writer need no PDF-specific logic at all as a result.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from dataclasses import asdict

import boto3

from common.chunking import compute_partitions
from common.db import create_failed_file_status_record, create_file_status_record
from common.models import WorkerMessage
from common.pdf import extract_pdf_text

WORKER_QUEUE_URL = os.environ.get("WORKER_QUEUE_URL")
FILE_STATUS_TABLE = os.environ.get("FILE_STATUS_TABLE")
NUM_PARTITIONS = int(os.environ.get("NUM_PARTITIONS", "10"))
MAX_PDF_BYTES = int(os.environ.get("MAX_PDF_BYTES", str(50 * 1024 * 1024)))

EXTRACTED_TEXT_SUFFIX = ".extracted"


class DispatchValidationError(ValueError):
    """Permanently-invalid input (bad extension, empty file, oversized or
    textless PDF) -- redelivery of the same S3 event would hit the identical
    error every time, so the caller records a failed file_status and does not
    let SQS retry this message."""


# Module-scoped so warm Lambda invocations reuse the same client instead of
# re-resolving the credential chain on every message.
s3 = boto3.client("s3")
sqs = boto3.client("sqs")


def handler(event, context):
    for record in event["Records"]:
        s3_event = json.loads(record["body"])
        _dispatch_s3_event(s3_event)


def _dispatch_s3_event(s3_event: dict) -> None:
    for s3_record in s3_event.get("Records", []):
        s3_info = s3_record["s3"]
        s3_bucket = s3_info["bucket"]["name"]
        file_id = urllib.parse.unquote_plus(s3_info["object"]["key"])
        original_size_bytes = s3_info["object"]["size"]

        try:
            worker_s3_key, worker_size_bytes = _resolve_worker_object(
                s3_bucket, file_id, original_size_bytes
            )
            partitions = compute_partitions(worker_size_bytes, NUM_PARTITIONS)
        except DispatchValidationError as e:
            create_failed_file_status_record(
                table_name=FILE_STATUS_TABLE,
                file_id=file_id,
                s3_bucket=s3_bucket,
                s3_key=file_id,
                file_size_bytes=original_size_bytes,
                error=str(e),
            )
            continue

        total_workers = len(partitions)

        created = create_file_status_record(
            table_name=FILE_STATUS_TABLE,
            file_id=file_id,
            s3_bucket=s3_bucket,
            s3_key=file_id,
            file_size_bytes=original_size_bytes,
            total_workers=total_workers,
        )
        if not created:
            # Redelivery of an already-dispatched event: skip re-fanning-out
            # worker-queue messages, since that would duplicate work.
            continue

        for worker_index, (start_byte, end_byte) in enumerate(partitions):
            message = WorkerMessage(
                file_id=file_id,
                s3_bucket=s3_bucket,
                s3_key=worker_s3_key,
                file_size_bytes=worker_size_bytes,
                worker_index=worker_index,
                total_workers=total_workers,
                start_byte=start_byte,
                end_byte=end_byte,
            )
            sqs.send_message(
                QueueUrl=WORKER_QUEUE_URL,
                MessageBody=json.dumps(asdict(message)),
            )


def _resolve_worker_object(
    s3_bucket: str, file_id: str, original_size_bytes: int
) -> tuple[str, int]:
    """Determine which S3 object (and byte size) partitioning/worker fan-out
    should run against, validating the upload along the way. Raises
    `DispatchValidationError` for anything permanently wrong with the input.
    """
    key_lower = file_id.lower()

    if key_lower.endswith(".pdf"):
        if original_size_bytes > MAX_PDF_BYTES:
            raise DispatchValidationError(f"PDF exceeds MAX_PDF_BYTES: file_id={file_id!r}")
        worker_s3_key, worker_size_bytes = _extract_and_stage_pdf_text(s3, s3_bucket, file_id)
    elif key_lower.endswith(".txt"):
        worker_s3_key, worker_size_bytes = file_id, original_size_bytes
    else:
        # Real uploads are already gated by the S3 notification's
        # filter_suffix -- this only guards local/simulated-event paths.
        raise DispatchValidationError(f"unsupported file extension: file_id={file_id!r}")

    if worker_size_bytes <= 0:
        raise DispatchValidationError(f"empty file: file_id={file_id!r}")

    return worker_s3_key, worker_size_bytes


def _extract_and_stage_pdf_text(s3, bucket: str, file_id: str) -> tuple[str, int]:
    """Download `file_id`'s PDF, extract its text, and stage that text as a new
    S3 object workers can byte-range partition like any other plain-text file.

    Returns (staged_s3_key, staged_text_size_bytes). Redelivery of the same
    dispatch-queue message re-runs this (wasteful but not incorrect, since the
    re-upload is idempotent) before `create_file_status_record`'s conditional
    put short-circuits further down -- the partition count depends on the
    extracted size, so there's no way to check idempotency any earlier.
    """
    pdf_bytes = s3.get_object(Bucket=bucket, Key=file_id)["Body"].read()
    text = extract_pdf_text(pdf_bytes)
    if not text.strip():
        raise DispatchValidationError(f"no extractable text in PDF: file_id={file_id!r}")

    encoded = text.encode("utf-8")
    extracted_key = f"extracted/{file_id}{EXTRACTED_TEXT_SUFFIX}"
    s3.put_object(
        Bucket=bucket,
        Key=extracted_key,
        Body=encoded,
        ContentType="text/plain; charset=utf-8",
    )
    return extracted_key, len(encoded)
