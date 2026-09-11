from common.chunking import chunk_text, estimate_tokens, trim_partition_bytes

# ---- estimate_tokens ----------------------------------------------------


def test_estimate_tokens_basic_ratio():
    assert estimate_tokens("a" * 400) == 100


def test_estimate_tokens_never_zero_for_nonempty():
    assert estimate_tokens("hi") == 1


# ---- chunk_text ----------------------------------------------------------


def test_chunk_text_empty_input():
    assert chunk_text("") == []


def test_chunk_text_short_input_single_chunk():
    text = "just a short sentence"
    chunks = chunk_text(text, target_tokens=500, max_tokens=8192, overlap_tokens=50)
    assert chunks == [text]


def test_chunk_text_respects_hard_cap():
    text = "word " * 2000  # 10000 chars, no natural short break near target
    max_tokens = 200
    chunks = chunk_text(text, target_tokens=100, max_tokens=max_tokens, overlap_tokens=0)
    assert all(len(c) <= max_tokens * 4 for c in chunks)


def test_chunk_text_hard_cap_enforced_with_no_whitespace():
    text = "a" * 5000
    max_tokens = 200
    chunks = chunk_text(text, target_tokens=100, max_tokens=max_tokens, overlap_tokens=0)
    assert all(len(c) <= max_tokens * 4 for c in chunks)
    assert len(chunks) > 1


def test_chunk_text_overlap_correctness():
    words = [f"word{i:04d}" for i in range(300)]
    text = " ".join(words)
    chunks = chunk_text(text, target_tokens=50, max_tokens=100, overlap_tokens=10)
    assert len(chunks) >= 2
    for a, b in zip(chunks, chunks[1:], strict=False):
        a_tail = set(a.split()[-5:])
        b_head = set(b.split()[:5])
        assert a_tail & b_head, f"expected shared words at boundary: {a_tail} vs {b_head}"


def test_chunk_text_no_overlap_when_configured_zero():
    words = [f"word{i:04d}" for i in range(300)]
    text = " ".join(words)
    chunks = chunk_text(text, target_tokens=50, max_tokens=100, overlap_tokens=0)
    seen = set()
    for c in chunks:
        c_words = set(c.split())
        assert not (seen & c_words), "expected no shared words with zero overlap"
        seen |= c_words


# ---- trim_partition_bytes -------------------------------------------------


def test_trim_captures_full_trailing_word_mid_word_cut():
    raw = b"hello world foo bar baz"
    # nominal partition covers "hello wo" (indices 0..7, mid-word cut on "world")
    result = trim_partition_bytes(raw, core_length=8, is_first=True, is_last=False)
    assert result == "hello world"


def test_trim_drops_leading_fragment_for_non_first_partition():
    raw = b"rld foo bar baz"
    result = trim_partition_bytes(raw, core_length=0, is_first=False, is_last=True)
    assert result == "foo bar baz"


def test_trim_middle_partition_both_edges():
    raw = b"rld foo ba"
    # core covers "rld foo b" (0..8), overlap tail is "a"
    result = trim_partition_bytes(raw, core_length=9, is_first=False, is_last=False)
    assert result == "foo ba"


def test_trim_first_partition_keeps_byte_zero():
    raw = b"hello world"
    result = trim_partition_bytes(raw, core_length=5, is_first=True, is_last=True)
    assert result == "hello world"


def test_trim_last_partition_keeps_eof():
    raw = b"hello world"
    result = trim_partition_bytes(raw, core_length=5, is_first=False, is_last=True)
    # leading trim still applies: drop up to first whitespace
    assert result == "world"


def test_trim_fallback_when_no_whitespace_found():
    raw = b"nowhitespacehere"
    result = trim_partition_bytes(raw, core_length=5, is_first=False, is_last=False)
    assert result == "nowhitespacehere"


def test_trim_is_utf8_safe_across_multibyte_chars():
    text = "café résumé naïve über"
    raw = text.encode("utf-8")
    space_idx = raw.index(b" ")
    result = trim_partition_bytes(raw, core_length=space_idx, is_first=True, is_last=True)
    assert result == text
