"""Partitioning-boundary trimming and token-window chunking for the JSONL
transcript format (one `{"start", "end", "text"}` object per line) staged by
dispatcher's fake-mode audio path and transcribe_completion's real path.

A sibling of common/chunking.py rather than an extension of it: the input
shape here is a list of typed segments, not a flat string, so boundary
trimming snaps to newlines instead of whitespace, and windowing packs whole
segments instead of splitting mid-string. Only the genuinely shared constant
(CHARS_PER_TOKEN) is imported rather than shared.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from common.chunking import CHARS_PER_TOKEN

logger = logging.getLogger(__name__)


@dataclass
class TranscriptSegment:
    start: float
    end: float
    text: str


def parse_jsonl_segments(text: str) -> list[TranscriptSegment]:
    """Parse one TranscriptSegment per non-blank line. A malformed or
    incomplete line (e.g. a partition boundary that clipped a trailing
    segment despite the overlap buffer) is logged and skipped, not fatal --
    mirrors common/pdf.py's per-page skip-and-continue tolerance."""
    segments: list[TranscriptSegment] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            segments.append(
                TranscriptSegment(
                    start=float(obj["start"]), end=float(obj["end"]), text=str(obj["text"])
                )
            )
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            logger.warning("skipping malformed transcript segment line", exc_info=True)
    return segments


def _find_first_newline_byte(data: bytes, start: int, stop: int | None = None) -> int | None:
    stop = len(data) if stop is None else stop
    idx = data.find(b"\n", start, stop)
    return idx if idx != -1 else None


def trim_transcript_partition_bytes(
    raw: bytes, core_length: int, is_first: bool, is_last: bool
) -> str:
    """Newline-snapped sibling of common.chunking.trim_partition_bytes: scans
    for the first b"\\n" byte instead of any whitespace byte, so a space
    embedded inside a JSONL segment's own `text` field is never mistaken for
    a partition boundary. Safe because a serialized JSON object never
    contains a literal newline byte (json.dumps escapes it) -- so the first
    b"\\n" found truly terminates a complete line, never a string value.

    Same edge logic as trim_partition_bytes: every partition but the last
    extends into the overlap region to capture its trailing line in full;
    every partition but the first drops its own leading partial line
    (already captured by the previous partition's trailing-edge scan).
    """
    if is_last:
        end_idx = len(raw)
    else:
        nl = _find_first_newline_byte(raw, core_length)
        end_idx = nl if nl is not None else len(raw)

    if is_first:
        start_idx = 0
    else:
        nl = _find_first_newline_byte(raw, 0, end_idx)
        start_idx = nl + 1 if nl is not None else 0
        start_idx = min(start_idx, end_idx)

    return raw[start_idx:end_idx].decode("utf-8", errors="ignore")


def chunk_transcript_text(
    text: str,
    target_tokens: int = 500,
    max_tokens: int = 8192,
    overlap_tokens: int = 50,
) -> list[tuple[str, float, float]]:
    """Pack parsed JSONL segments into token-budget windows at whole-segment
    granularity (never split mid-segment, unlike chunk_text's mid-string
    splits) -- reasonable since transcript segments are sentence-sized, not
    huge blocks, and splitting one would require inventing a timestamp for
    the sub-piece that the source data doesn't provide.

    Returns (chunk_text, start_time, end_time) tuples, where start_time/
    end_time are the first/last segment's own start/end in that window.
    Consecutive windows overlap by carrying trailing segments (up to
    ~overlap_tokens worth) into the next window's start, mirroring
    chunk_text's overlap behavior.
    """
    segments = parse_jsonl_segments(text)
    if not segments:
        return []

    target_chars = target_tokens * CHARS_PER_TOKEN
    max_chars = max_tokens * CHARS_PER_TOKEN
    overlap_chars = overlap_tokens * CHARS_PER_TOKEN
    n = len(segments)

    windows: list[tuple[str, float, float]] = []
    start = 0
    while start < n:
        # Always include at least one segment, even if it alone exceeds the
        # budget, so a single oversized segment can't stall progress.
        end = start + 1
        total_chars = len(segments[start].text)
        while end < n:
            seg_len = len(segments[end].text)
            if total_chars + seg_len > max_chars:
                break
            if total_chars + seg_len > target_chars:
                break
            total_chars += seg_len
            end += 1

        window = segments[start:end]
        chunk_body = " ".join(s.text for s in window)
        windows.append((chunk_body, window[0].start, window[-1].end))

        if end >= n:
            break

        # Carry trailing segments within overlap_chars into the next window;
        # always advance by at least one segment so this loop terminates.
        overlap_start = end
        overlap_total = 0
        while overlap_start > start:
            seg_len = len(segments[overlap_start - 1].text)
            if overlap_total + seg_len > overlap_chars:
                break
            overlap_total += seg_len
            overlap_start -= 1
        start = max(overlap_start, start + 1)

    return windows
