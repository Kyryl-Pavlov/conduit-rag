"""db_writer Lambda: upserts embedded chunks into the vector store and
advances per-partition/file completion tracking.

Triggered by write-queue (SQS), batch_size=10. Opens one VectorDBClient per
invocation, closed in `finally` -- mirrors "one Aurora connection per
invocation." Every step below is idempotent (see common/db.py's
mark_chunk_completion), so if any message in a batch raises and the whole
batch is retried, reprocessing already-completed messages is a harmless
no-op rather than double-counting completion.
"""

from __future__ import annotations

import json
import os

from common.db import (
    increment_chunks_written,
    increment_completed_workers,
    mark_chunk_completion,
    mark_file_indexed,
)
from common.vectordb import VectorDBClient

FILE_STATUS_TABLE = os.environ.get("FILE_STATUS_TABLE")
CHUNK_COMPLETION_TABLE = os.environ.get("CHUNK_COMPLETION_TABLE")


def handler(event, context):
    db = VectorDBClient()
    try:
        for record in event["Records"]:
            message = json.loads(record["body"])
            _process_message(message, db)
    finally:
        db.close()


def _process_message(message: dict, db: VectorDBClient) -> None:
    chunk_id = message["chunk_id"]
    file_id = message["file_id"]
    worker_index = message["worker_index"]
    vector = message["vector"]
    text = message["text"]
    metadata = message.get("metadata", {})
    chunk_count = metadata["chunk_count"]
    total_workers = metadata["total_workers"]

    db.upsert_chunk(chunk_id=chunk_id, file_id=file_id, text=text, vector=vector, metadata=metadata)

    created = mark_chunk_completion(CHUNK_COMPLETION_TABLE, chunk_id)
    if not created:
        # Already counted by a prior attempt (redelivery of this exact
        # message) -- stop here, or chunks_written/completed_workers would
        # be double-counted.
        return

    chunks_written = increment_chunks_written(FILE_STATUS_TABLE, file_id, worker_index)
    if chunks_written < chunk_count:
        return

    completed_workers = increment_completed_workers(FILE_STATUS_TABLE, file_id)
    if completed_workers >= total_workers:
        mark_file_indexed(FILE_STATUS_TABLE, file_id)
