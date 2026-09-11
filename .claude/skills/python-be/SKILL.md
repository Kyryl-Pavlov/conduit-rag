---
name: python-be
description: Use when writing or reviewing Python in dispatcher, worker, db_writer, transcribe_completion, or src/common — Lambda handler conventions, idempotency rules, dual-backend provider toggles, testing/lint setup for this repo's backend pipeline.
---

# Conduit RAG — Python backend conventions

Applies to `src/dispatcher`, `src/worker`, `src/db_writer`, `src/transcribe_completion`, and `src/common`. These are SQS-chained AWS Lambdas (plus one EventBridge-triggered Lambda); see the root `CLAUDE.md` for the pipeline overview before making changes here.

## Conventions this codebase actually follows

**Message/record shapes are dataclasses**, not dicts (`common/models.py`: `WorkerMessage`, `WriteMessage`). Status/format discriminators are `StrEnum` (`FileStatus`, `PartitionFormat`), not raw string literals scattered through the code, even though the wire format is still plain JSON strings via `asdict()`.

**boto3 clients are module-scoped**, never constructed inside the handler function — `s3 = boto3.client("s3")` at module level in every handler, so a warm Lambda invocation reuses the client instead of re-resolving the credential chain per message. Match this in any new handler.

**Idempotency is load-bearing, not optional.** SQS is at-least-once; every handler must survive redelivery without double-counting:
- Conditional DynamoDB puts (`attribute_not_exists`) are the idempotency primitive (`create_file_status_record`, `mark_chunk_completion` in `common/db.py`). A failed condition means "already done, skip the rest of this handler" — never treat it as an error.
- IDs are deterministic, not random: `chunk_id` (`file_id_w{worker_index}_c{local_chunk_num}`), Transcribe job names (`sha256(file_id)`), so redelivery reproduces the same identifiers instead of creating duplicates.
- It's fine to tolerate wasteful-but-harmless re-execution ahead of the actual idempotency check (e.g. `transcribe_completion` re-parses and re-uploads the same transcript on redelivery before `fan_out_partitions`'s conditional put short-circuits) rather than inventing an earlier dedup check.

**Error handling splits on retryability.** Permanently-invalid input (bad extension, empty file, oversized/textless PDF, empty transcript) raises a dedicated exception (`DispatchValidationError`) that the handler catches to write a `status=failed` `file_status` record — so SQS stops retrying a doomed message. Anything transient just propagates uncaught; SQS's own visibility-timeout/`maxReceiveCount`/DLQ handles retry. Don't add a `try/except` around transient failures just to log-and-swallow them.

**Dual-backend provider toggles** for anything with no local emulator: `EMBEDDINGS_PROVIDER=bedrock|fake`, `DB_MODE=data_api|psycopg`, `TRANSCRIPTION_PROVIDER=transcribe|fake`. The `fake` branch must be deterministic (seed a `random.Random` off a stable id like `file_id`, never true randomness) and "retrieval-meaningless but free/offline" — it exists so `infra/local`'s docker-compose stack works fully offline, not to approximate real output quality.

**Format-specific logic lives in sibling modules**, not format-branches bolted onto one file. `common/transcript_chunking.py` is a sibling of `common/chunking.py` — different input shape (typed JSONL segments vs. flat string) justifies a separate module; only genuinely shared constants (`CHARS_PER_TOKEN`) are imported across them. A handler that dispatches on a format discriminator reads it with `.get(key, DEFAULT)`, never a bare subscript — old in-flight messages predating the field must still decode correctly.

**Comments explain non-obvious *why*, and this codebase leans on that heavily** — module and function docstrings routinely justify a design decision, an invariant, or what breaks if changed (e.g. why JSONL line-splitting on `b"\n"` is safe, why a Sid gets `Resource = "*"`). When extending an existing file, match its existing comment density — don't strip "why" comments, and don't add comments that merely restate what a line does.

## Testing and lint

- `pytest` — `pythonpath=src`, so `from common.x import y` resolves without an install step.
- `tests/unit/` — pure functions only, no AWS (chunking, trimming, parsing, format math). New pure logic belongs here first.
- `tests/integration/` — moto-mocked AWS (handler-level behavior touching S3/SQS/DynamoDB).
- `ruff check .` / `ruff format .` — `select = ["E","F","I","UP","B"]`, `line-length = 100`, `target-version = "py312"` (StrEnum, `X | None`, etc. are fine to use).

## Procedure when touching this code

1. Read the target module's docstring first — they're long on purpose and usually state the actual invariant you need to not break.
2. For a new code path, ask: does this need redelivery/idempotency handling? Does it touch a service `infra/local` can't emulate (needs a fake/local-dev toggle)?
3. Add unit tests for new pure logic, integration (moto) tests for new AWS-touching handler behavior.
4. Run `ruff check . && ruff format . && pytest` before calling it done.

See also [[terraform-and-aws]] for the IAM/layer side of adding a new Lambda, and [[multimodal-rag]] for the specific pattern used to onboard a new file modality into this pipeline.
