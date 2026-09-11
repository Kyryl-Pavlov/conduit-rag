"""Shared message/record shapes for the dispatcher -> worker -> write-queue pipeline."""

from dataclasses import dataclass, field
from enum import StrEnum


class FileStatus(StrEnum):
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


class PartitionFormat(StrEnum):
    """What shape of bytes a WorkerMessage's partition holds -- not to be
    confused with an S3/HTTP "content type" (MIME type), hence the distinct
    name. TEXT is flat prose (.txt, or PDF-extracted text); TRANSCRIPT and
    VIDEO are both newline-delimited JSON segments (see
    common/transcript_chunking.py) with an identical wire shape but different
    provenance -- TRANSCRIPT from speech-to-text, VIDEO from visual
    object/product detection -- kept as distinct values so that provenance
    survives into each chunk's metadata (see worker/handler.py)."""

    TEXT = "text"
    TRANSCRIPT = "transcript"
    VIDEO = "video"


@dataclass
class WorkerMessage:
    """One dispatch-queue -> worker-queue message: a single byte-range partition to embed."""

    file_id: str
    s3_bucket: str
    s3_key: str
    file_size_bytes: int
    worker_index: int
    total_workers: int
    start_byte: int
    end_byte: int
    redrive_count: int = 0
    partition_format: str = PartitionFormat.TEXT.value


@dataclass
class WriteMessage:
    """One worker-queue -> write-queue message: a single embedded chunk."""

    chunk_id: str
    file_id: str
    worker_index: int
    vector: list[float]
    text: str
    metadata: dict = field(default_factory=dict)
