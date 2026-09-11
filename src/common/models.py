"""Shared message/record shapes for the dispatcher -> worker -> write-queue pipeline."""

from dataclasses import dataclass, field
from enum import StrEnum


class FileStatus(StrEnum):
    PROCESSING = "processing"
    INDEXED = "indexed"
    FAILED = "failed"


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


@dataclass
class WriteMessage:
    """One worker-queue -> write-queue message: a single embedded chunk."""

    chunk_id: str
    file_id: str
    worker_index: int
    vector: list[float]
    text: str
    metadata: dict = field(default_factory=dict)
