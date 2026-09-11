"""video_completion Lambda: the async second half of real-mode video dispatch.

Invoked DIRECTLY by the video-analysis Step Functions state machine (see
infra/cloud/modules/video_pipeline) via a native `arn:aws:states:::lambda:invoke`
task state -- `event` is a plain dict the state machine itself constructed
(`{"file_id", "s3_bucket", "status", "error"?}`), not an SQS or EventBridge
envelope. `dispatcher/handler.py`'s `_dispatch_video_upload` (real-mode
branch) starts the execution and returns without creating a `file_status`
record; this Lambda runs once the state machine's Fargate task (src/video_task)
has finished and does the staging/fan-out `dispatcher` does synchronously for
.txt/.pdf/fake-mode video.

Unlike `transcribe_completion`, no tag-based identity recovery is needed:
Step Functions carries `file_id`/`s3_bucket` straight through as the
execution's own JSON input, since dispatcher passed them in at
`start_execution` time (see common/video.start_video_analysis_execution).

The Fargate task reports success purely via process exit code (0 or
nonzero); the state machine's own Catch turns a nonzero exit into the
`status: "FAILED"` branch here, so this Lambda never needs to know *why* the
task failed beyond whatever `error` Step Functions forwards from its own
error object.
"""

from __future__ import annotations

import os

import boto3

from common.db import create_failed_file_status_record
from common.fanout import fan_out_partitions
from common.models import PartitionFormat

WORKER_QUEUE_URL = os.environ.get("WORKER_QUEUE_URL")
FILE_STATUS_TABLE = os.environ.get("FILE_STATUS_TABLE")
NUM_PARTITIONS = int(os.environ.get("NUM_PARTITIONS", "10"))

EXTRACTED_TEXT_SUFFIX = ".extracted"

# Module-scoped so warm Lambda invocations reuse the same client instead of
# re-resolving the credential chain on every invocation.
s3 = boto3.client("s3")


def handler(event, context):
    file_id = event["file_id"]
    s3_bucket = event["s3_bucket"]
    status = event["status"]

    if status == "FAILED":
        original_size_bytes = s3.head_object(Bucket=s3_bucket, Key=file_id)["ContentLength"]
        create_failed_file_status_record(
            table_name=FILE_STATUS_TABLE,
            file_id=file_id,
            s3_bucket=s3_bucket,
            s3_key=file_id,
            file_size_bytes=original_size_bytes,
            error=str(event.get("error", "video analysis failed")),
        )
        return

    # SUCCEEDED: the Fargate task already staged its JSONL result here.
    extracted_key = f"extracted/{file_id}{EXTRACTED_TEXT_SUFFIX}"
    worker_size_bytes = s3.head_object(Bucket=s3_bucket, Key=extracted_key)["ContentLength"]
    original_size_bytes = s3.head_object(Bucket=s3_bucket, Key=file_id)["ContentLength"]

    fan_out_partitions(
        file_id=file_id,
        s3_bucket=s3_bucket,
        original_s3_key=file_id,
        original_file_size_bytes=original_size_bytes,
        worker_s3_key=extracted_key,
        worker_size_bytes=worker_size_bytes,
        num_partitions=NUM_PARTITIONS,
        worker_queue_url=WORKER_QUEUE_URL,
        file_status_table=FILE_STATUS_TABLE,
        partition_format=PartitionFormat.VIDEO.value,
    )
