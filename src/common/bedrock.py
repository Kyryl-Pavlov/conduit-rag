"""Bedrock Titan Text Embeddings V2 wrapper.

`invoke_model` embeds exactly one text per call -- there is no native
multi-text batch input for synchronous invoke_model (true Bedrock batch
inference is an async job API, unsuitable for a per-Lambda-invocation flow).
`embed_batch` is therefore a sequential loop, not a single batched request.
Throttling resilience comes from the client's adaptive retry config, not
manual backoff.
"""

from __future__ import annotations

import hashlib
import json
import os
import random

import boto3
from botocore.config import Config

DEFAULT_MODEL_ID = "amazon.titan-embed-text-v2:0"
DEFAULT_DIMENSIONS = 1024

# Bedrock has no local/Docker emulator. For local dev (docker-compose), set
# EMBEDDINGS_PROVIDER=fake to skip the network call entirely -- deterministic,
# free, and offline, at the cost of the vectors being retrieval-meaningless.
EMBEDDINGS_PROVIDER = os.environ.get("EMBEDDINGS_PROVIDER", "bedrock")


def _fake_embed(text: str, dimensions: int) -> list[float]:
    """Deterministic stand-in for a real embedding: seeded by a hash of the
    text, so identical chunks always produce identical vectors."""
    seed = int(hashlib.sha256(text.encode("utf-8")).hexdigest(), 16)
    rng = random.Random(seed)
    return [rng.uniform(-1.0, 1.0) for _ in range(dimensions)]


def _client():
    # Lazy (not module-level) so tests can patch this module without needing
    # real credentials, and so the Lambda runtime's own region resolution applies.
    #
    # AWS_BEDROCK_* is a distinct credential source from AWS_ACCESS_KEY_ID /
    # AWS_SECRET_ACCESS_KEY: in local docker-compose, the latter are dummy
    # values MinIO/DynamoDB-local require (see docker-compose.yml's
    # aws-local-credentials anchor) and cannot authenticate against real AWS.
    # In production there's no such collision -- these env vars are unset and
    # the Lambda execution role's default credential chain applies unchanged.
    kwargs: dict = {}
    if os.environ.get("AWS_BEDROCK_ACCESS_KEY_ID"):
        kwargs["aws_access_key_id"] = os.environ["AWS_BEDROCK_ACCESS_KEY_ID"]
        kwargs["aws_secret_access_key"] = os.environ["AWS_BEDROCK_SECRET_ACCESS_KEY"]
        if os.environ.get("AWS_BEDROCK_SESSION_TOKEN"):
            kwargs["aws_session_token"] = os.environ["AWS_BEDROCK_SESSION_TOKEN"]
    if os.environ.get("AWS_BEDROCK_REGION"):
        kwargs["region_name"] = os.environ["AWS_BEDROCK_REGION"]
    return boto3.client(
        "bedrock-runtime",
        config=Config(retries={"max_attempts": 5, "mode": "adaptive"}),
        **kwargs,
    )


def _invoke(client, text: str, model_id: str, dimensions: int) -> list[float]:
    response = client.invoke_model(
        modelId=model_id,
        body=json.dumps({"inputText": text, "dimensions": dimensions, "normalize": True}),
    )
    payload = json.loads(response["body"].read())
    return payload["embedding"]


def embed_text(
    text: str,
    model_id: str = DEFAULT_MODEL_ID,
    dimensions: int = DEFAULT_DIMENSIONS,
) -> list[float]:
    """Embed a single text via Titan Text Embeddings V2."""
    if EMBEDDINGS_PROVIDER == "fake":
        return _fake_embed(text, dimensions)
    return _invoke(_client(), text, model_id, dimensions)


def embed_batch(
    texts: list[str],
    model_id: str = DEFAULT_MODEL_ID,
    dimensions: int = DEFAULT_DIMENSIONS,
) -> list[list[float]]:
    """Embed multiple texts as a sequential loop of single-text invoke_model calls."""
    if EMBEDDINGS_PROVIDER == "fake":
        return [_fake_embed(t, dimensions) for t in texts]
    client = _client()
    return [_invoke(client, t, model_id, dimensions) for t in texts]
