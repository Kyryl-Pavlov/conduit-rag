import json

import pytest

import common.transcription as transcription_module
from common.transcription import (
    fake_transcript_jsonl,
    parse_transcript_json_to_jsonl,
    recover_upload_identity,
    sanitize_job_name,
    transcribe_output_key,
    transcription_job_arn,
)

# ---- sanitize_job_name / transcribe_output_key / transcription_job_arn ----


def test_sanitize_job_name_is_deterministic():
    assert sanitize_job_name("uploads/a.mp3") == sanitize_job_name("uploads/a.mp3")


def test_sanitize_job_name_differs_for_different_input():
    assert sanitize_job_name("uploads/a.mp3") != sanitize_job_name("uploads/b.mp3")


def test_sanitize_job_name_is_transcribe_charset_safe():
    name = sanitize_job_name("uploads/weird name with spaces/日本語.mp3")
    assert all(c.isalnum() or c in "._-" for c in name)
    assert len(name) <= 200


def test_transcribe_output_key_is_deterministic_and_namespaced():
    key = transcribe_output_key("uploads/a.mp3")
    assert key == f"transcribe-output/{sanitize_job_name('uploads/a.mp3')}.json"


def test_transcription_job_arn_format():
    arn = transcription_job_arn("us-east-1", "123456789012", "conduit-abc")
    assert arn == "arn:aws:transcribe:us-east-1:123456789012:transcription-job/conduit-abc"


# ---- fake_transcript_jsonl --------------------------------------------------


def test_fake_transcript_jsonl_is_deterministic():
    assert fake_transcript_jsonl("uploads/a.mp3") == fake_transcript_jsonl("uploads/a.mp3")


def test_fake_transcript_jsonl_differs_for_different_input():
    assert fake_transcript_jsonl("uploads/a.mp3") != fake_transcript_jsonl("uploads/b.mp3")


def test_fake_transcript_jsonl_produces_valid_ascending_segments():
    lines = fake_transcript_jsonl("uploads/a.mp3").decode("utf-8").splitlines()
    assert len(lines) >= 1
    prev_end = -1.0
    for line in lines:
        obj = json.loads(line)
        assert set(obj) == {"start", "end", "text"}
        assert obj["start"] >= prev_end
        assert obj["end"] > obj["start"]
        assert obj["text"]
        prev_end = obj["end"]


# ---- parse_transcript_json_to_jsonl -----------------------------------------


def _pronunciation(word: str, start: float, end: float) -> dict:
    return {
        "type": "pronunciation",
        "start_time": str(start),
        "end_time": str(end),
        "alternatives": [{"confidence": "1.0", "content": word}],
    }


def _punctuation(mark: str) -> dict:
    return {"type": "punctuation", "alternatives": [{"confidence": "1.0", "content": mark}]}


def test_parse_transcript_flushes_on_sentence_end():
    items = [
        _pronunciation("Hello", 0.0, 0.5),
        _pronunciation("world", 0.6, 1.0),
        _punctuation("."),
        _pronunciation("Goodbye", 1.5, 2.0),
        _punctuation("."),
    ]
    result = parse_transcript_json_to_jsonl({"results": {"items": items}})
    lines = [json.loads(line) for line in result.decode("utf-8").splitlines()]
    assert lines == [
        {"start": 0.0, "end": 1.0, "text": "Hello world."},
        {"start": 1.5, "end": 2.0, "text": "Goodbye."},
    ]


def test_parse_transcript_falls_back_to_word_count_without_punctuation():
    items = [_pronunciation(f"word{i}", float(i), float(i) + 0.5) for i in range(120)]
    result = parse_transcript_json_to_jsonl({"results": {"items": items}})
    lines = [json.loads(line) for line in result.decode("utf-8").splitlines()]
    assert len(lines) >= 2  # _MAX_SEGMENT_WORDS forces a flush before 120 words accrue
    assert sum(len(line["text"].split()) for line in lines) == 120


def test_parse_transcript_empty_items_produces_empty_bytes():
    assert parse_transcript_json_to_jsonl({"results": {"items": []}}) == b""


def test_parse_transcript_drops_lone_leading_punctuation():
    assert parse_transcript_json_to_jsonl({"results": {"items": [_punctuation(",")]}}) == b""


# ---- recover_upload_identity -------------------------------------------------


class _FakeTranscribeClient:
    def __init__(self, tags: dict[str, str]):
        self._tags = tags

    def list_tags_for_resource(self, ResourceArn):  # noqa: N803 -- matches boto3's param casing
        return {"Tags": [{"Key": k, "Value": v} for k, v in self._tags.items()]}


def test_recover_upload_identity_reads_tags_back(monkeypatch):
    fake_client = _FakeTranscribeClient(
        {"conduit-file-id": "uploads/a.mp3", "conduit-s3-bucket": "my-bucket"}
    )
    monkeypatch.setattr(transcription_module, "_transcribe_client", lambda: fake_client)

    file_id, s3_bucket = recover_upload_identity(
        "arn:aws:transcribe:us-east-1:123456789012:transcription-job/x"
    )
    assert file_id == "uploads/a.mp3"
    assert s3_bucket == "my-bucket"


def test_recover_upload_identity_raises_if_tags_missing(monkeypatch):
    monkeypatch.setattr(
        transcription_module, "_transcribe_client", lambda: _FakeTranscribeClient({})
    )

    with pytest.raises(KeyError):
        recover_upload_identity("arn:aws:transcribe:us-east-1:123456789012:transcription-job/x")


# ---- provider default --------------------------------------------------------


def test_default_provider_is_transcribe():
    # Guards against accidentally shipping "fake" as the default in prod,
    # mirroring test_bedrock_fake.py's test_default_provider_is_bedrock.
    assert transcription_module.TRANSCRIPTION_PROVIDER == "transcribe"
