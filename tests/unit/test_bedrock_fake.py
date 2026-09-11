import common.bedrock as bedrock_module


def _with_fake_provider(monkeypatch):
    monkeypatch.setattr(bedrock_module, "EMBEDDINGS_PROVIDER", "fake")


def test_fake_embed_text_is_deterministic(monkeypatch):
    _with_fake_provider(monkeypatch)
    a = bedrock_module.embed_text("hello world", dimensions=16)
    b = bedrock_module.embed_text("hello world", dimensions=16)
    assert a == b


def test_fake_embed_text_differs_for_different_input(monkeypatch):
    _with_fake_provider(monkeypatch)
    a = bedrock_module.embed_text("hello world", dimensions=16)
    b = bedrock_module.embed_text("goodbye world", dimensions=16)
    assert a != b


def test_fake_embed_respects_dimensions(monkeypatch):
    _with_fake_provider(monkeypatch)
    vector = bedrock_module.embed_text("hello", dimensions=32)
    assert len(vector) == 32


def test_fake_embed_batch_matches_embed_text(monkeypatch):
    _with_fake_provider(monkeypatch)
    texts = ["a", "b", "a"]
    batch = bedrock_module.embed_batch(texts, dimensions=8)
    assert batch[0] == batch[2]  # identical input -> identical vector
    assert batch[0] == bedrock_module.embed_text("a", dimensions=8)


def test_default_provider_is_bedrock():
    # Guards against accidentally shipping "fake" as the default in prod.
    # (monkeypatch reverts EMBEDDINGS_PROVIDER after each test, so this reflects
    # the module's real, unpatched state.)
    assert bedrock_module.EMBEDDINGS_PROVIDER == "bedrock"
