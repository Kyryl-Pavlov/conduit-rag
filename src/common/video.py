"""Visual object/product detection integration for uploaded video.

Pluggable provider abstraction (VIDEO_PROVIDER=<name>|fake, mirrors
common/bedrock.py's EMBEDDINGS_PROVIDER toggle) plus local-dev fake JSONL
fabrication (mirrors common/transcription.py's fake_transcript_jsonl) and the
real-path entrypoint, which starts a Step Functions execution instead of a
single synchronous boto3 call -- unlike Transcribe, the real provider's work
runs on a Fargate task (src/video_task/), not inside this Lambda, since a
vision-LLM API call/poll can run well past what's reasonable to hold a
15-minute-capped Lambda open for, and Fargate has no such ceiling. See
infra/cloud/modules/video_pipeline for the orchestration.

Only VIDEO_PROVIDER=fake is implemented in this pass. Any other value starts
a real Step Functions execution whose Fargate task will call analyze_video()
below -- which is presently a stub (NotImplementedError) since no real
provider integration exists yet. VIDEO_PROVIDER therefore defaults to "fake"
here, the OPPOSITE polarity of TRANSCRIPTION_PROVIDER's "transcribe" default
-- there is no real provider to safely default prod to yet. Flip this default
once one exists, mirroring TRANSCRIPTION_PROVIDER's convention.
"""

from __future__ import annotations

import hashlib
import json
import os
import random

import boto3

VIDEO_EXTENSIONS = frozenset({".mp4", ".mov", ".webm"})

VIDEO_PROVIDER = os.environ.get("VIDEO_PROVIDER", "fake")

_FAKE_OBJECT_COUNT = (
    60  # sized like transcription.py's _FAKE_SEGMENT_COUNT -- see its comment for why
)
_FAKE_OBJECT_NAMES = (
    "red sneaker",
    "coffee mug",
    "laptop",
    "backpack",
    "bicycle",
    "houseplant",
    "desk lamp",
    "sunglasses",
    "water bottle",
    "office chair",
)

EXECUTION_NAME_PREFIX = "conduit-"


def sanitize_execution_name(file_id: str) -> str:
    """Deterministic, Step-Functions-charset-safe execution name derived from
    `file_id` -- same rationale as transcription.sanitize_job_name: execution
    names disallow whitespace and most punctuation, and S3 keys can contain
    both, so this hashes rather than mangles. Also what makes StartExecution
    idempotent under redelivery (see start_video_analysis_execution)."""
    digest = hashlib.sha256(file_id.encode("utf-8")).hexdigest()
    return f"{EXECUTION_NAME_PREFIX}{digest}"


def _step_functions_client():
    # Lazy (not module-level) so tests can patch this module without needing
    # real credentials, mirroring common/bedrock.py's _client() and
    # common/transcription.py's _transcribe_client().
    return boto3.client("stepfunctions")


def fake_video_objects_jsonl(file_id: str) -> bytes:
    """Local-dev stand-in for a real vision-LLM video analysis job: fabricates
    deterministic (seeded by file_id) placeholder "object appeared" segments
    with plausible timestamps -- same JSONL {"start","end","text"} shape
    common/transcript_chunking.py already parses/chunks, so video reuses that
    module completely unchanged."""
    seed = int(hashlib.sha256(file_id.encode("utf-8")).hexdigest(), 16)
    rng = random.Random(seed)

    lines: list[str] = []
    t = 0.0
    for i in range(_FAKE_OBJECT_COUNT):
        duration = rng.uniform(1.5, 5.0)
        start, end = round(t, 2), round(t + duration, 2)
        obj = rng.choice(_FAKE_OBJECT_NAMES)
        lines.append(
            json.dumps({"start": start, "end": end, "text": f"Detected object: {obj} (#{i})."})
        )
        t = end
    return ("\n".join(lines) + "\n").encode("utf-8")


def start_video_analysis_execution(state_machine_arn: str, s3_bucket: str, file_id: str) -> str:
    """Start a real (Step Functions + Fargate) video analysis execution.
    Returns the deterministic execution name (mirrors start_transcription_job
    returning job_name, not a full ARN -- dispatcher discards this return
    value either way).

    Unlike Transcribe (which has no native way to attach caller metadata
    queryable before a file_status record exists, forcing dispatcher to tag
    the job and transcribe_completion to read tags back via
    recover_upload_identity), a Step Functions execution simply carries
    (file_id, s3_bucket) forward as its own JSON input -- no analogous
    recovery step is needed anywhere downstream.

    Redelivery of the same dispatch-queue message re-calls this with the same
    deterministic execution name; a Standard workflow rejects a duplicate
    execution name with ExecutionAlreadyExists -- treated as "already
    started," mirroring start_transcription_job's ConflictException handling.

    ASSUMPTION, verify against real AWS: exact boto3 exception name/behavior
    of ExecutionAlreadyExists.
    """
    name = sanitize_execution_name(file_id)
    client = _step_functions_client()
    try:
        client.start_execution(
            stateMachineArn=state_machine_arn,
            name=name,
            input=json.dumps({"file_id": file_id, "s3_bucket": s3_bucket}),
        )
    except client.exceptions.ExecutionAlreadyExists:
        pass
    return name


def analyze_video(local_video_path: str, provider: str) -> bytes:
    """Real-provider entrypoint, called only from src/video_task/main.py
    (Fargate), never from a Lambda. NOT IMPLEMENTED in this pass -- only
    VIDEO_PROVIDER=fake exists, and fake mode never reaches this function at
    all (dispatcher fabricates fake_video_objects_jsonl() directly and never
    starts a Step Functions execution). This stub establishes the interface a
    real provider integration will fill in: local video path in, JSONL bytes
    (same {"start","end","text"} shape) out -- keeping any of the provider's
    own async submit/poll flow entirely inside this call, so Step Functions/
    the state machine never needs to know about it.
    """
    raise NotImplementedError(
        f"VIDEO_PROVIDER={provider!r} has no implementation yet -- only 'fake' is supported, "
        "and fake mode is handled entirely in dispatcher, never reaching this function."
    )
