import json

from common.transcript_chunking import (
    TranscriptSegment,
    chunk_transcript_text,
    parse_jsonl_segments,
    trim_transcript_partition_bytes,
)


def _line(start: float, end: float, text: str) -> str:
    return json.dumps({"start": start, "end": end, "text": text})


# ---- parse_jsonl_segments ----------------------------------------------------


def test_parse_jsonl_segments_basic():
    text = "\n".join([_line(0.0, 1.0, "hello"), _line(1.0, 2.0, "world")])
    assert parse_jsonl_segments(text) == [
        TranscriptSegment(start=0.0, end=1.0, text="hello"),
        TranscriptSegment(start=1.0, end=2.0, text="world"),
    ]


def test_parse_jsonl_segments_skips_malformed_lines():
    text = "\n".join([_line(0.0, 1.0, "good"), "not json at all", _line(1.0, 2.0, "also good")])
    assert [s.text for s in parse_jsonl_segments(text)] == ["good", "also good"]


def test_parse_jsonl_segments_skips_lines_missing_fields():
    text = "\n".join([_line(0.0, 1.0, "good"), json.dumps({"start": 0.0})])
    assert [s.text for s in parse_jsonl_segments(text)] == ["good"]


def test_parse_jsonl_segments_ignores_blank_lines():
    text = _line(0.0, 1.0, "hello") + "\n\n" + _line(1.0, 2.0, "world")
    assert len(parse_jsonl_segments(text)) == 2


def test_parse_jsonl_segments_empty_input():
    assert parse_jsonl_segments("") == []


# ---- trim_transcript_partition_bytes -----------------------------------------


def test_trim_transcript_does_not_treat_embedded_space_as_boundary():
    # The segment's own `text` field contains spaces -- only the newline
    # between lines is a real partition boundary.
    raw = (_line(0.0, 1.0, "hello there world") + "\n").encode("utf-8")
    result = trim_transcript_partition_bytes(raw, core_length=5, is_first=True, is_last=True)
    assert json.loads(result) == {"start": 0.0, "end": 1.0, "text": "hello there world"}


def test_trim_transcript_captures_full_trailing_line_mid_line_cut():
    first_line = _line(0.0, 1.0, "hello")
    raw = (first_line + "\n" + _line(1.0, 2.0, "world")).encode("utf-8")
    core = len(first_line.encode("utf-8")) // 2  # cut mid-way through the first line
    result = trim_transcript_partition_bytes(raw, core_length=core, is_first=True, is_last=False)
    assert json.loads(result) == {"start": 0.0, "end": 1.0, "text": "hello"}


def test_trim_transcript_drops_leading_fragment_for_non_first_partition():
    clipped_fragment = _line(0.0, 1.0, "hello")[
        3:
    ]  # simulate a line clipped by the previous partition
    second_line = _line(1.0, 2.0, "world")
    raw = (clipped_fragment + "\n" + second_line).encode("utf-8")
    result = trim_transcript_partition_bytes(raw, core_length=0, is_first=False, is_last=True)
    assert json.loads(result) == {"start": 1.0, "end": 2.0, "text": "world"}


def test_trim_transcript_last_partition_keeps_eof_with_no_trailing_newline():
    raw = _line(0.0, 1.0, "hello").encode("utf-8")
    result = trim_transcript_partition_bytes(raw, core_length=5, is_first=True, is_last=True)
    assert json.loads(result) == {"start": 0.0, "end": 1.0, "text": "hello"}


def test_trim_transcript_fallback_when_no_newline_found():
    raw = b"no newline in this partition at all"
    result = trim_transcript_partition_bytes(raw, core_length=5, is_first=False, is_last=False)
    assert result == "no newline in this partition at all"


# ---- chunk_transcript_text -----------------------------------------------------


def test_chunk_transcript_text_empty_input():
    assert chunk_transcript_text("") == []


def test_chunk_transcript_text_single_window_for_short_input():
    lines = [_line(float(i), float(i) + 1.0, f"segment{i:04d}") for i in range(3)]
    windows = chunk_transcript_text(
        "\n".join(lines), target_tokens=500, max_tokens=8192, overlap_tokens=50
    )
    assert len(windows) == 1
    chunk, start, end = windows[0]
    assert "segment0000" in chunk and "segment0002" in chunk
    assert start == 0.0
    assert end == 3.0


def test_chunk_transcript_text_respects_target_tokens():
    long_segment_text = "word " * 30  # 150 chars, comfortably over a small target
    lines = [_line(float(i), float(i) + 1.0, long_segment_text) for i in range(20)]
    windows = chunk_transcript_text(
        "\n".join(lines), target_tokens=50, max_tokens=8192, overlap_tokens=0
    )
    assert len(windows) > 1


def test_chunk_transcript_text_never_splits_a_segment_across_windows():
    long_text = "x" * 10_000  # a single segment alone exceeds max_tokens*4 chars
    windows = chunk_transcript_text(
        _line(0.0, 1.0, long_text), target_tokens=10, max_tokens=20, overlap_tokens=0
    )
    assert len(windows) == 1
    assert windows[0][0] == long_text


def test_chunk_transcript_text_start_end_span_window_segments():
    lines = [_line(float(i), float(i) + 0.5, f"seg{i}") for i in range(10)]
    windows = chunk_transcript_text(
        "\n".join(lines), target_tokens=2, max_tokens=8192, overlap_tokens=0
    )
    assert len(windows) > 1
    for _chunk, start, end in windows:
        assert start < end


def test_chunk_transcript_text_overlap_correctness():
    lines = [_line(float(i), float(i) + 1.0, f"seg{i:04d}") for i in range(10)]  # 7 chars each
    windows = chunk_transcript_text(
        "\n".join(lines), target_tokens=8, max_tokens=8192, overlap_tokens=4
    )
    assert len(windows) >= 2
    for (a, _, _), (b, _, _) in zip(windows, windows[1:], strict=False):
        a_tail = set(a.split()[-2:])
        b_head = set(b.split()[:2])
        assert a_tail & b_head, f"expected shared segments at boundary: {a_tail} vs {b_head}"


def test_chunk_transcript_text_no_overlap_when_configured_zero():
    lines = [_line(float(i), float(i) + 1.0, f"seg{i:04d}") for i in range(10)]
    windows = chunk_transcript_text(
        "\n".join(lines), target_tokens=8, max_tokens=8192, overlap_tokens=0
    )
    seen: set[str] = set()
    for chunk, _start, _end in windows:
        words = set(chunk.split())
        assert not (seen & words), "expected no shared segments with zero overlap"
        seen |= words
