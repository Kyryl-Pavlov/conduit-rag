"""DynamoDB helpers.

`dispatcher` creates the `file_status` record. `db_writer` is the only
writer of `chunk_completion`, and the only thing that advances
`file_status.chunks_written`/`completed_workers`/`status` -- see
db_writer/handler.py for the per-partition completion algorithm this
supports.
"""

from __future__ import annotations

from datetime import UTC, datetime

import boto3
from botocore.exceptions import ClientError

from common.models import FileStatus


def _table(table_name: str):
    # Function-scoped (not module-level) so moto's mock context can intercept it in tests.
    return boto3.resource("dynamodb").Table(table_name)


def _now_iso() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def create_file_status_record(
    table_name: str,
    file_id: str,
    s3_bucket: str,
    s3_key: str,
    file_size_bytes: int,
    total_workers: int,
) -> bool:
    """Idempotently create the file_status row for a newly-dispatched file.

    Returns True if this call created the record, False if one already existed
    (e.g. redelivery of the same S3 event) -- the caller should treat False as
    a no-op skip, not an error.
    """
    now = _now_iso()
    try:
        _table(table_name).put_item(
            Item={
                "file_id": file_id,
                "s3_bucket": s3_bucket,
                "s3_key": s3_key,
                "file_size_bytes": file_size_bytes,
                "status": FileStatus.PROCESSING.value,
                "total_workers": total_workers,
                "completed_workers": 0,
                "chunks_written": {},
                "created_at": now,
                "updated_at": now,
            },
            ConditionExpression="attribute_not_exists(file_id)",
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def create_failed_file_status_record(
    table_name: str,
    file_id: str,
    s3_bucket: str,
    s3_key: str,
    file_size_bytes: int,
    error: str,
) -> bool:
    """Idempotently create a terminal `failed` file_status row for input that can
    never successfully dispatch (bad extension, empty file, oversized/textless PDF).

    Unlike a transient failure -- which the caller lets propagate so SQS's own
    retry/DLQ machinery handles it -- these are permanent, so the caller does not
    re-raise. Without this, such a file never gets a file_status row at all and
    silently vanishes from the frontend instead of showing as failed.
    """
    now = _now_iso()
    try:
        _table(table_name).put_item(
            Item={
                "file_id": file_id,
                "s3_bucket": s3_bucket,
                "s3_key": s3_key,
                "file_size_bytes": file_size_bytes,
                "status": FileStatus.FAILED.value,
                "total_workers": 0,
                "completed_workers": 0,
                "chunks_written": {},
                "error": error,
                "created_at": now,
                "updated_at": now,
            },
            ConditionExpression="attribute_not_exists(file_id)",
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def mark_chunk_completion(table_name: str, chunk_id: str) -> bool:
    """Idempotently record that `chunk_id` has been durably written.

    Returns True if this call created the record, False if one already
    existed (redelivery of the same write-queue message) -- the caller
    should stop there, not repeat the chunks_written/completed_workers
    increments that follow a first-time write.
    """
    try:
        _table(table_name).put_item(
            Item={"chunk_id": chunk_id, "completed": True},
            ConditionExpression="attribute_not_exists(chunk_id)",
        )
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def increment_chunks_written(table_name: str, file_id: str, worker_index: int) -> int:
    """Atomically increment the count of chunks written for one partition.

    Returns the new count for that partition (not the whole file).
    """
    response = _table(table_name).update_item(
        Key={"file_id": file_id},
        UpdateExpression="ADD chunks_written.#idx :one",
        ExpressionAttributeNames={"#idx": str(worker_index)},
        ExpressionAttributeValues={":one": 1},
        ReturnValues="UPDATED_NEW",
    )
    return int(response["Attributes"]["chunks_written"][str(worker_index)])


def increment_completed_workers(table_name: str, file_id: str) -> int:
    """Atomically increment the count of fully-written partitions for a file."""
    response = _table(table_name).update_item(
        Key={"file_id": file_id},
        UpdateExpression="ADD completed_workers :one",
        ExpressionAttributeValues={":one": 1},
        ReturnValues="UPDATED_NEW",
    )
    return int(response["Attributes"]["completed_workers"])


def mark_file_indexed(table_name: str, file_id: str) -> None:
    _table(table_name).update_item(
        Key={"file_id": file_id},
        UpdateExpression="SET #status = :indexed, updated_at = :now",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":indexed": FileStatus.INDEXED.value, ":now": _now_iso()},
    )
