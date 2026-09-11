"""Shared dispatch tail: partition a resolved plain-text/transcript S3 object
and fan out one WorkerMessage per partition to the worker queue.

Used by both dispatcher (the .txt/.pdf/fake-transcript synchronous path) and
transcribe_completion (the real-Transcribe async completion path) -- the two
Lambdas differ only in *how* they arrive at a worker_s3_key/worker_size_bytes
pointing at partitionable bytes; everything from "how many partitions" onward
is identical, so it lives here once instead of twice.
"""

from __future__ import annotations

import json
from dataclasses import asdict

import boto3

from common.chunking import compute_partitions
from common.db import create_file_status_record
from common.models import PartitionFormat, WorkerMessage

# Module-scoped so warm Lambda invocations reuse the same client instead of
# re-resolving the credential chain on every message (mirrors dispatcher.py).
sqs = boto3.client("sqs")


def fan_out_partitions(
    *,
    file_id: str,
    s3_bucket: str,
    original_s3_key: str,
    original_file_size_bytes: int,
    worker_s3_key: str,
    worker_size_bytes: int,
    num_partitions: int,
    worker_queue_url: str,
    file_status_table: str,
    partition_format: str = PartitionFormat.TEXT.value,
) -> bool:
    """Idempotently create the file_status record and fan out one
    WorkerMessage per byte-range partition of `worker_s3_key`.

    `original_s3_key`/`original_file_size_bytes` are what the file_status
    record shows the frontend (the user's original upload); `worker_s3_key`/
    `worker_size_bytes` are what workers actually byte-range-fetch and may
    point at a staged stand-in object instead (PDF-extracted text, or a
    transcript's staged JSONL) -- same split dispatcher already makes for PDF.

    Returns False (no-op) if file_status already existed for `file_id`
    (redelivery of the same dispatch/completion event) -- the caller should
    treat that as "already dispatched," not an error. Returns True if this
    call performed the fan-out.
    """
    partitions = compute_partitions(worker_size_bytes, num_partitions)
    total_workers = len(partitions)

    created = create_file_status_record(
        table_name=file_status_table,
        file_id=file_id,
        s3_bucket=s3_bucket,
        s3_key=original_s3_key,
        file_size_bytes=original_file_size_bytes,
        total_workers=total_workers,
    )
    if not created:
        return False

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
            partition_format=partition_format,
        )
        sqs.send_message(
            QueueUrl=worker_queue_url,
            MessageBody=json.dumps(asdict(message)),
        )
    return True
