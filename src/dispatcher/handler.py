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

Partitioning and worker fan-out then run identically for .txt/.pdf, against
whichever object (original .txt, or extracted-text stand-in for .pdf) holds
plain text: `WorkerMessage.s3_key`/`file_size_bytes` point at that text
object, while `file_id` (and the `file_status` record's `s3_key`/
`file_size_bytes`, used for status display) always stay the original upload's
key/size. worker/db_writer need no PDF-specific logic at all as a result. The
shared partition/create-file_status/fan-out tail lives in
`common.fanout.fan_out_partitions`.

Audio files (see `common.transcription.AUDIO_EXTENSIONS`) are the third,
structurally different exception: transcription is an async job that can run
for minutes, so dispatcher can't extract-and-continue synchronously the way
it does for PDF. See `_dispatch_audio_upload`'s docstring for the two-phase
split this implies.

Video files (see `common.video.VIDEO_EXTENSIONS`) are a fourth exception,
structurally identical in shape to audio's two-phase split but orchestrated
differently: the async work (a pluggable, possibly-slow vision-LLM call) runs
on a Fargate task via Step Functions instead of AWS Transcribe, since there's
no managed AWS service for this. See `_dispatch_video_upload`'s docstring.
"""

from __future__ import annotations

import json
import os
import urllib.parse

import boto3

from common.db import create_failed_file_status_record
from common.fanout import fan_out_partitions
from common.models import PartitionFormat
from common.pdf import extract_pdf_text
from common.transcription import (
    AUDIO_EXTENSIONS,
    TRANSCRIPTION_PROVIDER,
    fake_transcript_jsonl,
    start_transcription_job,
)
from common.video import (
    VIDEO_EXTENSIONS,
    VIDEO_PROVIDER,
    fake_video_objects_jsonl,
    start_video_analysis_execution,
)

WORKER_QUEUE_URL = os.environ.get("WORKER_QUEUE_URL")
FILE_STATUS_TABLE = os.environ.get("FILE_STATUS_TABLE")
NUM_PARTITIONS = int(os.environ.get("NUM_PARTITIONS", "10"))
MAX_PDF_BYTES = int(os.environ.get("MAX_PDF_BYTES", str(50 * 1024 * 1024)))
MAX_AUDIO_BYTES = int(os.environ.get("MAX_AUDIO_BYTES", str(2 * 1024 * 1024 * 1024)))
MAX_VIDEO_BYTES = int(os.environ.get("MAX_VIDEO_BYTES", str(10 * 1024 * 1024 * 1024)))
VIDEO_STATE_MACHINE_ARN = os.environ.get("VIDEO_STATE_MACHINE_ARN")

EXTRACTED_TEXT_SUFFIX = ".extracted"


class DispatchValidationError(ValueError):
    """Permanently-invalid input (bad extension, empty file, oversized or
    textless PDF, oversized or empty audio/video) -- redelivery of the same
    S3 event would hit the identical error every time, so the caller records
    a failed file_status and does not let SQS retry this message."""


# Module-scoped so warm Lambda invocations reuse the same client instead of
# re-resolving the credential chain on every message.
s3 = boto3.client("s3")


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
            if _is_audio_upload(file_id):
                _dispatch_audio_upload(s3_bucket, file_id, original_size_bytes)
                continue
            if _is_video_upload(file_id):
                _dispatch_video_upload(s3_bucket, file_id, original_size_bytes)
                continue
            worker_s3_key, worker_size_bytes = _resolve_worker_object(
                s3_bucket, file_id, original_size_bytes
            )
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

        fan_out_partitions(
            file_id=file_id,
            s3_bucket=s3_bucket,
            original_s3_key=file_id,
            original_file_size_bytes=original_size_bytes,
            worker_s3_key=worker_s3_key,
            worker_size_bytes=worker_size_bytes,
            num_partitions=NUM_PARTITIONS,
            worker_queue_url=WORKER_QUEUE_URL,
            file_status_table=FILE_STATUS_TABLE,
        )


def _is_audio_upload(file_id: str) -> bool:
    return file_id.lower().endswith(tuple(AUDIO_EXTENSIONS))


def _dispatch_audio_upload(s3_bucket: str, file_id: str, original_size_bytes: int) -> None:
    """Audio can't reuse `_resolve_worker_object`'s synchronous
    (key, size)-returning contract: real transcription is an async job that
    can run for minutes, unlike pypdf's in-process, single-invocation parse.

    `TRANSCRIPTION_PROVIDER=fake` (local dev, no Transcribe/EventBridge
    emulator exists) fabricates a transcript inline and fans out immediately,
    in this same invocation -- same shape as the .txt/.pdf tail.
    `TRANSCRIPTION_PROVIDER=transcribe` (real) starts the job and returns; the
    `transcribe_completion` Lambda (triggered by EventBridge once the job
    finishes) stages the real transcript and calls `fan_out_partitions` from
    there instead. Either way, no `file_status` record exists for this file
    until whichever path's `fan_out_partitions` call runs -- so a real-mode
    audio upload is invisible in the frontend for the duration of
    transcription. Accepted, documented limitation (see CLAUDE.md's Current
    State section) rather than a placeholder "transcribing" status.

    Raises `DispatchValidationError` for permanently-invalid input (empty or
    oversized audio), same contract as `_resolve_worker_object`.
    """
    if original_size_bytes <= 0:
        raise DispatchValidationError(f"empty file: file_id={file_id!r}")
    if original_size_bytes > MAX_AUDIO_BYTES:
        raise DispatchValidationError(f"audio file exceeds MAX_AUDIO_BYTES: file_id={file_id!r}")

    if TRANSCRIPTION_PROVIDER == "fake":
        jsonl_bytes = fake_transcript_jsonl(file_id)
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
        return

    start_transcription_job(s3_bucket, file_id)


def _is_video_upload(file_id: str) -> bool:
    return file_id.lower().endswith(tuple(VIDEO_EXTENSIONS))


def _dispatch_video_upload(s3_bucket: str, file_id: str, original_size_bytes: int) -> None:
    """Video's real path is structurally like audio's two-phase split, but
    the async work (a pluggable, possibly-slow vision-LLM call) runs on a
    Fargate task orchestrated by Step Functions instead of AWS Transcribe --
    see infra/cloud/modules/video_pipeline and src/video_task. There's no
    managed AWS service for "detect objects in this video," so unlike audio
    this pipeline owns the compute for the real path, not just the
    completion callback.

    `VIDEO_PROVIDER=fake` (local dev, no Step Functions/ECS emulator exists)
    fabricates a placeholder object-detection JSONL inline and fans out
    immediately, in this same invocation -- same shape as the fake-mode audio
    tail. `VIDEO_PROVIDER=<anything else>` (real) starts a Step Functions
    execution and returns; the `video_completion` Lambda (invoked directly by
    that state machine once its Fargate task finishes) stages the real result
    and calls `fan_out_partitions` from there instead. Either way, no
    `file_status` record exists for this file until whichever path's
    `fan_out_partitions` call runs -- same accepted invisible-while-processing
    limitation already documented for real-mode audio.

    Raises `DispatchValidationError` for permanently-invalid input (empty or
    oversized video), same contract as `_dispatch_audio_upload`.
    """
    if original_size_bytes <= 0:
        raise DispatchValidationError(f"empty file: file_id={file_id!r}")
    if original_size_bytes > MAX_VIDEO_BYTES:
        raise DispatchValidationError(f"video file exceeds MAX_VIDEO_BYTES: file_id={file_id!r}")

    if VIDEO_PROVIDER == "fake":
        jsonl_bytes = fake_video_objects_jsonl(file_id)
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
            partition_format=PartitionFormat.VIDEO.value,
        )
        return

    start_video_analysis_execution(VIDEO_STATE_MACHINE_ARN, s3_bucket, file_id)


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
