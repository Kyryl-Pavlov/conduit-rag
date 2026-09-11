import json

import pytest

import common.video as video_module
from common.video import (
    fake_video_objects_jsonl,
    sanitize_execution_name,
    start_video_analysis_execution,
)

# ---- sanitize_execution_name ------------------------------------------------


def test_sanitize_execution_name_is_deterministic():
    assert sanitize_execution_name("uploads/a.mp4") == sanitize_execution_name("uploads/a.mp4")


def test_sanitize_execution_name_differs_for_different_input():
    assert sanitize_execution_name("uploads/a.mp4") != sanitize_execution_name("uploads/b.mp4")


def test_sanitize_execution_name_is_charset_safe():
    name = sanitize_execution_name("uploads/weird name with spaces/日本語.mp4")
    assert all(c.isalnum() or c in "._-" for c in name)
    assert len(name) <= 200


# ---- fake_video_objects_jsonl -----------------------------------------------


def test_fake_video_objects_jsonl_is_deterministic():
    assert fake_video_objects_jsonl("uploads/a.mp4") == fake_video_objects_jsonl("uploads/a.mp4")


def test_fake_video_objects_jsonl_differs_for_different_input():
    assert fake_video_objects_jsonl("uploads/a.mp4") != fake_video_objects_jsonl("uploads/b.mp4")


def test_fake_video_objects_jsonl_produces_valid_ascending_segments():
    lines = fake_video_objects_jsonl("uploads/a.mp4").decode("utf-8").splitlines()
    assert len(lines) >= 1
    prev_end = -1.0
    for line in lines:
        obj = json.loads(line)
        assert set(obj) == {"start", "end", "text"}
        assert obj["start"] >= prev_end
        assert obj["end"] > obj["start"]
        assert obj["text"]
        prev_end = obj["end"]


# ---- start_video_analysis_execution -----------------------------------------


class _FakeStepFunctionsClient:
    class _Exceptions:
        class ExecutionAlreadyExists(Exception):
            pass

    def __init__(self, raise_conflict: bool = False):
        self.exceptions = self._Exceptions
        self.calls: list[dict] = []
        self._raise_conflict = raise_conflict

    def start_execution(self, **kwargs):
        self.calls.append(kwargs)
        if self._raise_conflict:
            raise self.exceptions.ExecutionAlreadyExists()


def test_start_video_analysis_execution_calls_start_execution(monkeypatch):
    fake_client = _FakeStepFunctionsClient()
    monkeypatch.setattr(video_module, "_step_functions_client", lambda: fake_client)

    name = start_video_analysis_execution(
        "arn:aws:states:us-east-1:123456789012:stateMachine:x", "my-bucket", "uploads/a.mp4"
    )

    assert name == sanitize_execution_name("uploads/a.mp4")
    assert len(fake_client.calls) == 1
    call = fake_client.calls[0]
    assert call["stateMachineArn"] == "arn:aws:states:us-east-1:123456789012:stateMachine:x"
    assert call["name"] == name
    assert json.loads(call["input"]) == {"file_id": "uploads/a.mp4", "s3_bucket": "my-bucket"}


def test_start_video_analysis_execution_treats_conflict_as_already_started(monkeypatch):
    fake_client = _FakeStepFunctionsClient(raise_conflict=True)
    monkeypatch.setattr(video_module, "_step_functions_client", lambda: fake_client)

    # Must not raise -- a duplicate execution name means "already started,"
    # mirroring start_transcription_job's ConflictException handling.
    name = start_video_analysis_execution(
        "arn:aws:states:us-east-1:123456789012:stateMachine:x", "my-bucket", "uploads/a.mp4"
    )
    assert name == sanitize_execution_name("uploads/a.mp4")


# ---- analyze_video stub ------------------------------------------------------


def test_analyze_video_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        video_module.analyze_video("/tmp/video.mp4", "some-real-provider")


# ---- provider default --------------------------------------------------------


def test_default_provider_is_fake():
    # Inverted polarity vs. TRANSCRIPTION_PROVIDER's "transcribe" default:
    # unlike Transcribe, no real video provider exists yet, so defaulting to
    # a real-sounding name would make every prod upload hit NotImplementedError.
    assert video_module.VIDEO_PROVIDER == "fake"
