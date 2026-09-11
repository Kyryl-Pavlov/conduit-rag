"""transcribe_completion Lambda: the async second half of audio dispatch.

Triggered by an EventBridge rule on AWS Transcribe's job-state-change event
(see infra/cloud/modules/lambda/main.tf), not SQS -- `event` is a raw
EventBridge event (`{"detail": {...}, ...}`), not `{"Records": [...]}`.
`dispatcher/handler.py`'s `_dispatch_audio_upload` (real-mode branch) starts
the Transcribe job and returns without creating a `file_status` record; this
Lambda picks up once that job reaches a terminal state and does the staging/
fan-out `dispatcher` does synchronously for .txt/.pdf.

ASSUMPTION, needs empirical verification against real AWS: the EventBridge
event shape (`source: "aws.transcribe"`, `detail-type:
"Transcribe Job State Change"`, `detail.TranscriptionJobName`/
`TranscriptionJobStatus`) and the Transcribe job ARN format used to look up
this job's tags. See the plan's "Assumptions to verify" section.

There's no DynamoDB record yet at job-start time to stash the original
upload's identity in, so `dispatcher` tags the job with it
(`common.transcription.TAG_FILE_ID`/`TAG_S3_BUCKET`) and this Lambda reads
those tags back via `recover_upload_identity` instead.
"""

from __future__ import annotations

import json
import os

import boto3

from common.db import create_failed_file_status_record
from common.fanout import fan_out_partitions
from common.models import PartitionFormat
from common.transcription import (
    parse_transcript_json_to_jsonl,
    recover_upload_identity,
    transcribe_output_key,
    transcription_job_arn,
)

WORKER_QUEUE_URL = os.environ.get("WORKER_QUEUE_URL")
FILE_STATUS_TABLE = os.environ.get("FILE_STATUS_TABLE")
NUM_PARTITIONS = int(os.environ.get("NUM_PARTITIONS", "10"))

EXTRACTED_TEXT_SUFFIX = ".extracted"

# Module-scoped so warm Lambda invocations reuse the same client instead of
# re-resolving the credential chain on every message.
s3 = boto3.client("s3")
transcribe = boto3.client("transcribe")


def handler(event, context):
    job_name = event["detail"]["TranscriptionJobName"]
    status = event["detail"]["TranscriptionJobStatus"]
    _process_job(job_name, status, context)


def _process_job(job_name: str, status: str, context) -> None:
    region = os.environ["AWS_REGION"]
    account_id = context.invoked_function_arn.split(":")[4]
    job_arn = transcription_job_arn(region, account_id, job_name)
    # Raises KeyError if tags are missing -- should never happen for a job
    # this pipeline itself started; let it propagate and retry via
    # EventBridge's own async-invoke retry rather than mask it.
    file_id, s3_bucket = recover_upload_identity(job_arn)
    original_size_bytes = s3.head_object(Bucket=s3_bucket, Key=file_id)["ContentLength"]

    if status == "FAILED":
        job = transcribe.get_transcription_job(TranscriptionJobName=job_name)
        reason = job["TranscriptionJob"].get("FailureReason", "transcription job failed")
        create_failed_file_status_record(
            table_name=FILE_STATUS_TABLE,
            file_id=file_id,
            s3_bucket=s3_bucket,
            s3_key=file_id,
            file_size_bytes=original_size_bytes,
            error=reason,
        )
        return

    transcript_json = json.loads(
        s3.get_object(Bucket=s3_bucket, Key=transcribe_output_key(file_id))["Body"].read()
    )
    jsonl_bytes = parse_transcript_json_to_jsonl(transcript_json)
    if not jsonl_bytes.strip():
        create_failed_file_status_record(
            table_name=FILE_STATUS_TABLE,
            file_id=file_id,
            s3_bucket=s3_bucket,
            s3_key=file_id,
            file_size_bytes=original_size_bytes,
            error="empty transcript (no recognized speech)",
        )
        return

    # Redelivery of this same COMPLETED event re-runs the extraction/upload
    # (wasteful but not incorrect, since it's a deterministic re-parse of the
    # same Transcribe output onto the same key) before fan_out_partitions's
    # own conditional-put short-circuits the actual fan-out -- same tolerance
    # dispatcher's PDF path already has for the equivalent redelivery case.
    extracted_key = f"extracted/{file_id}{EXTRACTED_TEXT_SUFFIX}"
    s3.put_object(
        Bucket=s3_bucket,
        Key=extracted_key,
        Body=jsonl_bytes,
        ContentType="application/x-ndjson",
    )
    fan_out_partitions(
        file_id=file_id,
        s3_bucket=s3_bucket,
        original_s3_key=file_id,
        original_file_size_bytes=original_size_bytes,
        worker_s3_key=extracted_key,
        worker_size_bytes=len(jsonl_bytes),
        num_partitions=NUM_PARTITIONS,
        worker_queue_url=WORKER_QUEUE_URL,
        file_status_table=FILE_STATUS_TABLE,
        partition_format=PartitionFormat.TRANSCRIPT.value,
    )
