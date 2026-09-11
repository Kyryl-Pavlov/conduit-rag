"""Partitioning, byte-range boundary trimming, and token-window chunking.

No official tokenizer exists for Titan Text Embeddings V2 via pip, so every token
count in this module is an approximation: ``chars // 4``. That ratio is only
accurate for predominantly ASCII/Latin text.
"""

from __future__ import annotations

CHARS_PER_TOKEN = 4
_WHITESPACE_BYTES = b" \t\n\r"


def estimate_tokens(text: str) -> int:
    """Approximate token count for `text` using the chars/4 heuristic."""
    return max(1, len(text) // CHARS_PER_TOKEN)


def compute_partitions(file_size_bytes: int, num_partitions: int = 10) -> list[tuple[int, int]]:
    """Split a file into up to `num_partitions` inclusive [start, end] byte ranges.

    Always produces `min(num_partitions, file_size_bytes)` partitions of nearly
    equal size (the first `remainder` partitions absorb one extra byte each),
    covering [0, file_size_bytes - 1] with no gaps or overlaps. This bounds
    worst-case worker fan-out to `num_partitions` regardless of file size.
    """
    if file_size_bytes <= 0:
        raise ValueError("file_size_bytes must be positive")

    n = min(num_partitions, file_size_bytes)
    base, remainder = divmod(file_size_bytes, n)

    partitions: list[tuple[int, int]] = []
    start = 0
    for i in range(n):
        size = base + (1 if i < remainder else 0)
        end = start + size - 1
        partitions.append((start, end))
        start = end + 1
    return partitions


def _find_first_whitespace_byte(data: bytes, start: int, stop: int | None = None) -> int | None:
    stop = len(data) if stop is None else stop
    for i in range(start, stop):
        if data[i] in _WHITESPACE_BYTES:
            return i
    return None


def trim_partition_bytes(raw: bytes, core_length: int, is_first: bool, is_last: bool) -> str:
    """Trim a fetched byte range to clean word boundaries.

    `raw` spans [start_byte, fetched_end] where fetched_end extends `core_length`
    bytes (the partition's nominal [start_byte, end_byte] span) by an overlap
    buffer. Splitting exactly at a whitespace byte is always UTF-8-safe, since
    ASCII whitespace bytes can never appear as continuation bytes in a multi-byte
    UTF-8 sequence.

    - Trailing edge: every partition except the last one scans forward from
      `core_length` into the overlap region for the first whitespace byte and
      includes text up to (excluding) it -- capturing its trailing word in full.
    - Leading edge: every partition except the first one scans forward from byte
      0 for the first whitespace byte and starts just after it -- dropping its
      own leading partial word (already captured in full by the previous
      partition's trailing-edge scan).

    If no whitespace byte is found in the relevant region (extremely unlikely
    for realistic prose), the trim falls back to the full buffer / start-at-0,
    and any resulting partial trailing UTF-8 sequence is dropped on decode.
    """
    if is_last:
        end_idx = len(raw)
    else:
        ws = _find_first_whitespace_byte(raw, core_length)
        end_idx = ws if ws is not None else len(raw)

    if is_first:
        start_idx = 0
    else:
        ws = _find_first_whitespace_byte(raw, 0, end_idx)
        start_idx = ws + 1 if ws is not None else 0
        start_idx = min(start_idx, end_idx)

    return raw[start_idx:end_idx].decode("utf-8", errors="ignore")


def _find_first_whitespace_char(text: str, start: int, stop: int) -> int | None:
    for i in range(start, stop):
        if text[i].isspace():
            return i
    return None


def chunk_text(
    text: str,
    target_tokens: int = 500,
    max_tokens: int = 8192,
    overlap_tokens: int = 50,
) -> list[str]:
    """Split `text` into whitespace-snapped, overlapping fixed-token-limit windows.

    Windows target `target_tokens` and never exceed `max_tokens`. Consecutive
    chunks share roughly `overlap_tokens` of trailing/leading content, so
    retrieval isn't penalized for a relevant passage landing on a chunk seam.
    """
    if not text:
        return []

    target_chars = target_tokens * CHARS_PER_TOKEN
    max_chars = max_tokens * CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * CHARS_PER_TOKEN
    n = len(text)

    chunks: list[str] = []
    start = 0
    while start < n:
        nominal_end = min(start + target_chars, n)
        hard_end = min(start + max_chars, n)

        if nominal_end < n:
            ws = _find_first_whitespace_char(text, nominal_end, hard_end)
            end = ws if ws is not None else hard_end
        else:
            end = nominal_end

        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)

        if end >= n:
            break
        start = max(end - overlap_chars, start + 1)

    return chunks


def make_chunk_id(file_id: str, worker_index: int, local_chunk_num: int) -> str:
    """Deterministic chunk id -- no randomness, so SQS at-least-once redelivery
    reprocessing the same partition regenerates identical ids, keeping the
    downstream db_writer upsert idempotent."""
    return f"{file_id}_w{worker_index}_c{local_chunk_num}"
