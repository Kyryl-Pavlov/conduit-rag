"""AWS Transcribe integration: starting jobs, recovering upload identity from
a completed/failed job, and collapsing Transcribe's word-level output JSON
into the newline-delimited JSON ("JSONL") segment format the rest of the
pipeline partitions/chunks (see common/transcript_chunking.py).

Transcribe has no local emulator (same tier as Bedrock/the RDS Data API). For
local dev, set TRANSCRIPTION_PROVIDER=fake -- unlike Bedrock's fake mode
(which still runs through the same async-shaped code path with a stubbed
network call), fake transcription skips the real job entirely: dispatcher
calls fake_transcript_jsonl() synchronously and fans out immediately, since
there is nothing to poll or wait on locally. Real vs. fake control flow is
different enough (sync-with-immediate-fanout vs. async-return-early) that the
branch lives in the caller (dispatcher), not inside a single function here.

A JSONL line is always safe to split on a literal b"\\n" byte: each line is
one json.dumps()-serialized object, and JSON string encoding always escapes
newlines within a string value (to "\\n", two ASCII characters) rather than
emitting a raw 0x0A byte -- so a segment's own transcript text can never
introduce a false line boundary.
"""

from __future__ import annotations

import hashlib
import json
import os
import random

import boto3

AUDIO_EXTENSIONS = frozenset({".mp3", ".wav", ".m4a", ".flac"})

TRANSCRIPTION_PROVIDER = os.environ.get("TRANSCRIPTION_PROVIDER", "transcribe")
TRANSCRIBE_LANGUAGE_CODE = os.environ.get("TRANSCRIBE_LANGUAGE_CODE", "en-US")

JOB_NAME_PREFIX = "conduit-"
TAG_FILE_ID = "conduit-file-id"
TAG_S3_BUCKET = "conduit-s3-bucket"

_SENTENCE_END_PUNCTUATION = {".", "?", "!"}
_MAX_SEGMENT_WORDS = 50
# Large enough that, at the default NUM_PARTITIONS=10 byte-range fan-out, each
# partition's core reliably spans several complete JSONL lines rather than a
# sliver smaller than one -- a partition that lands entirely inside a single
# line finds no clean newline boundary and its fragment fails to parse,
# hitting the "partition sends zero chunks, file stuck at processing forever"
# gap CLAUDE.md's Current State section already documents for small files in
# general. A handful of short placeholder segments hit this on nearly every
# local-dev upload (observed empirically); this margin is chosen so the
# default docker-compose smoke test doesn't.
_FAKE_SEGMENT_COUNT = 60


def sanitize_job_name(file_id: str) -> str:
    """Deterministic, Transcribe-charset-safe job name derived from
    `file_id`. TranscriptionJobName only allows [0-9a-zA-Z._-] and S3 keys
    can contain slashes/spaces/unicode, so this hashes rather than mangles --
    also means redelivery of the same upload always recomputes the same job
    name, which is what makes StartTranscriptionJob idempotent (see
    start_transcription_job's ConflictException handling)."""
    digest = hashlib.sha256(file_id.encode("utf-8")).hexdigest()
    return f"{JOB_NAME_PREFIX}{digest}"


def transcribe_output_key(file_id: str) -> str:
    """S3 key Transcribe writes its output JSON to, and where
    transcribe_completion reads it back from. Both dispatcher (job start) and
    transcribe_completion (job read) compute this independently instead of
    parsing a URI out of the job response, so neither depends on the other's
    view of the job."""
    return f"transcribe-output/{sanitize_job_name(file_id)}.json"


def transcription_job_arn(region: str, account_id: str, job_name: str) -> str:
    return f"arn:aws:transcribe:{region}:{account_id}:transcription-job/{job_name}"


def _transcribe_client():
    # Lazy (not module-level) so tests can patch this module without needing
    # real credentials, mirroring common/bedrock.py's _client().
    return boto3.client("transcribe")


def start_transcription_job(s3_bucket: str, file_id: str) -> str:
    """Start a real Transcribe job for `file_id`, tagged so
    transcribe_completion can recover (file_id, s3_bucket) later -- there is
    no file_status record yet at this point to look them up in. Returns the
    job name.

    Redelivery of the same dispatch-queue message re-calls this with the same
    deterministic job name; Transcribe's own ConflictException on a duplicate
    name is treated as "already started," not an error, mirroring
    create_file_status_record's conditional-put idempotency elsewhere in this
    pipeline.
    """
    job_name = sanitize_job_name(file_id)
    client = _transcribe_client()
    try:
        client.start_transcription_job(
            TranscriptionJobName=job_name,
            LanguageCode=TRANSCRIBE_LANGUAGE_CODE,
            Media={"MediaFileUri": f"s3://{s3_bucket}/{file_id}"},
            OutputBucketName=s3_bucket,
            OutputKey=transcribe_output_key(file_id),
            Tags=[
                {"Key": TAG_FILE_ID, "Value": file_id},
                {"Key": TAG_S3_BUCKET, "Value": s3_bucket},
            ],
        )
    except client.exceptions.ConflictException:
        pass
    return job_name


def recover_upload_identity(job_arn: str) -> tuple[str, str]:
    """Read back (file_id, s3_bucket) from the tags start_transcription_job
    attached to this job. Raises KeyError if either tag is missing -- should
    never happen for a job this pipeline itself started."""
    client = _transcribe_client()
    response = client.list_tags_for_resource(ResourceArn=job_arn)
    tags = {tag["Key"]: tag["Value"] for tag in response.get("Tags", [])}
    return tags[TAG_FILE_ID], tags[TAG_S3_BUCKET]


def fake_transcript_jsonl(file_id: str) -> bytes:
    """Local-dev stand-in for a real Transcribe job: fabricates a handful of
    deterministic (seeded by file_id) placeholder segments with plausible
    timestamps instead of calling the real service -- as
    "retrieval-meaningless but free/offline" as bedrock.py's _fake_embed."""
    seed = int(hashlib.sha256(file_id.encode("utf-8")).hexdigest(), 16)
    rng = random.Random(seed)

    lines: list[str] = []
    t = 0.0
    for i in range(_FAKE_SEGMENT_COUNT):
        duration = rng.uniform(2.0, 6.0)
        start, end = round(t, 2), round(t + duration, 2)
        lines.append(
            json.dumps({"start": start, "end": end, "text": f"Fake transcript segment {i}."})
        )
        t = end
    return ("\n".join(lines) + "\n").encode("utf-8")


def parse_transcript_json_to_jsonl(transcribe_result: dict) -> bytes:
    """Collapse Transcribe's word-level `results.items` into sentence-bounded
    JSONL segments (one {"start", "end", "text"} object per line).

    A segment flushes on sentence-ending punctuation (./?/!) or after
    _MAX_SEGMENT_WORDS words, whichever comes first -- the word-count fallback
    covers transcripts with sparse or no punctuation. A trailing punctuation
    item with no preceding word (or any segment that ends up with no text) is
    dropped rather than emitted as a punctuation-only line.
    """
    items = transcribe_result.get("results", {}).get("items", [])

    lines: list[str] = []
    words: list[str] = []
    seg_start: float | None = None
    seg_end: float | None = None

    def flush() -> None:
        nonlocal words, seg_start, seg_end
        text = "".join(words).strip()
        if text and seg_start is not None:
            lines.append(json.dumps({"start": seg_start, "end": seg_end, "text": text}))
        words = []
        seg_start = None
        seg_end = None

    for item in items:
        alternatives = item.get("alternatives") or []
        content = alternatives[0].get("content", "") if alternatives else ""
        if not content:
            continue

        if item.get("type") == "punctuation":
            words.append(content)
            if content in _SENTENCE_END_PUNCTUATION:
                flush()
            continue

        start = float(item["start_time"])
        end = float(item["end_time"])
        if seg_start is None:
            seg_start = start
        seg_end = end
        words.append((" " if words else "") + content)
        if len(words) >= _MAX_SEGMENT_WORDS:
            flush()

    flush()
    return ("\n".join(lines) + ("\n" if lines else "")).encode("utf-8")
