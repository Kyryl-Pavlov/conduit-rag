"""Worker Lambda: embeds one byte-range partition of a text file and forwards
the resulting chunks to the write-queue.

Triggered by the worker-queue (SQS), one partition per invocation. This Lambda
never touches DynamoDB -- completion tracking is reserved for the future
db_writer Lambda (see the plan's Deviations section). On any exception this
handler just re-raises; standard SQS visibility-timeout-expiry and
`maxReceiveCount` handle retry/DLQ.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict

import boto3

from common.bedrock import DEFAULT_DIMENSIONS, DEFAULT_MODEL_ID, embed_batch
from common.chunking import chunk_text, make_chunk_id, trim_partition_bytes
from common.models import WriteMessage

WRITE_QUEUE_URL = os.environ.get("WRITE_QUEUE_URL")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", DEFAULT_MODEL_ID)
BEDROCK_EMBEDDING_DIM = int(os.environ.get("BEDROCK_EMBEDDING_DIM", str(DEFAULT_DIMENSIONS)))
TARGET_TOKENS_PER_CHUNK = int(os.environ.get("TARGET_TOKENS_PER_CHUNK", "500"))
MAX_TOKENS_PER_CHUNK = int(os.environ.get("MAX_TOKENS_PER_CHUNK", "8192"))
OVERLAP_TOKENS = int(os.environ.get("OVERLAP_TOKENS", "50"))
OVERLAP_BUFFER_BYTES = int(os.environ.get("OVERLAP_BUFFER_BYTES", "512"))

# Module-scoped so warm Lambda invocations reuse the same client instead of
# re-resolving the credential chain on every message.
s3 = boto3.client("s3")
sqs = boto3.client("sqs")


def handler(event, context):
    for record in event["Records"]:
        message = json.loads(record["body"])
        _process_partition(message)


def _process_partition(message: dict) -> None:
    file_id = message["file_id"]
    s3_bucket = message["s3_bucket"]
    s3_key = message["s3_key"]
    file_size_bytes = message["file_size_bytes"]
    worker_index = message["worker_index"]
    total_workers = message["total_workers"]
    start_byte = message["start_byte"]
    end_byte = message["end_byte"]

    is_first = worker_index == 0
    is_last = worker_index == total_workers - 1
    core_length = end_byte - start_byte + 1

    if is_last:
        fetch_end = end_byte
    else:
        fetch_end = min(end_byte + OVERLAP_BUFFER_BYTES - 1, file_size_bytes - 1)
    response = s3.get_object(Bucket=s3_bucket, Key=s3_key, Range=f"bytes={start_byte}-{fetch_end}")
    raw = response["Body"].read()

    text = trim_partition_bytes(raw, core_length, is_first=is_first, is_last=is_last)

    chunks = chunk_text(
        text,
        target_tokens=TARGET_TOKENS_PER_CHUNK,
        max_tokens=MAX_TOKENS_PER_CHUNK,
        overlap_tokens=OVERLAP_TOKENS,
    )
    if not chunks:
        return

    vectors = embed_batch(chunks, model_id=BEDROCK_MODEL_ID, dimensions=BEDROCK_EMBEDDING_DIM)

    for local_chunk_num, (chunk, vector) in enumerate(zip(chunks, vectors, strict=True)):
        write_message = WriteMessage(
            chunk_id=make_chunk_id(file_id, worker_index, local_chunk_num),
            file_id=file_id,
            worker_index=worker_index,
            vector=vector,
            text=chunk,
            metadata={
                "worker_start_byte": start_byte,
                "worker_end_byte": end_byte,
                "chunk_index": local_chunk_num,
                "chunk_count": len(chunks),
                "total_workers": total_workers,
                "embedding_model": BEDROCK_MODEL_ID,
                "embedding_dim": BEDROCK_EMBEDDING_DIM,
            },
        )
        sqs.send_message(
            QueueUrl=WRITE_QUEUE_URL,
            MessageBody=json.dumps(asdict(write_message)),
        )
