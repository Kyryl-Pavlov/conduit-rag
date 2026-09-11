# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Conduit RAG: an LLM-agnostic RAG (retrieval-augmented generation) system. Users upload text files through a Next.js frontend; an SQS-chained pipeline of AWS Lambdas partitions, embeds, and indexes them into a pgvector-backed Aurora Postgres store for retrieval.

## Commands

### Python (Lambdas: `dispatcher`, `worker`, `db_writer`, and `src/common`)

```bash
pip install -r requirements-dev.txt   # installs requirements.txt + pytest/moto/ruff
pytest                                 # run all tests (pythonpath=src, testpaths=tests, see pyproject.toml)
pytest tests/unit/test_chunking.py     # single file
pytest tests/unit/test_chunking.py::test_chunk_text_respects_max_tokens  # single test
pytest tests/unit          # unit only (pure functions, no AWS)
pytest tests/integration   # moto-mocked AWS integration tests
ruff check .               # lint
ruff format .               # format
```

### Frontend (`frontend/`, Next.js 16 + TypeScript)

```bash
cd frontend
npm install
npm run dev     # dev server, http://localhost:3000
npm run build
npm run lint
```

Next.js 16 postdates this assistant's training data and its own scaffolded `AGENTS.md` says so explicitly — read the relevant guide under `frontend/node_modules/next/dist/docs/` before writing App Router code, especially anything involving route handler `params` (`Promise<{...}>`, must `await`) or Tailwind v4's CSS-first config (no `tailwind.config.js`; see `app/globals.css`).

### Local end-to-end stack (docker-compose emulators)

```bash
cd infra/local
docker compose up -d --build   # postgres+pgvector, dynamodb-local, minio (S3), elasticmq (SQS), frontend, dispatcher/worker/db-writer pollers
python scripts/simulate_upload.py <path-to-a-.txt-file>   # run from the host, not a container
```

Or exercise the same flow through the real UI at `http://localhost:3000` (upload → status polling; the frontend's own API routes talk to the same emulators).

### Terraform (`infra/cloud/`)

```bash
cd infra/cloud
terraform init -backend-config=backend.hcl   # backend.hcl copied from backend.hcl.example (gitignored)
terraform validate
terraform fmt -recursive
terraform plan -var-file=dev.tfvars          # dev.tfvars copied from dev.tfvars.example (gitignored)
```

`infra/cloud/bootstrap/` is a separate, one-time-applied module (creates the S3 tfstate bucket + DynamoDB lock table) — don't fold it into the root apply. No `terraform apply` has been run against the root module yet; treat cloud infra as author/validate-only until told otherwise.

## Architecture

### Pipeline: dispatcher → worker → db_writer

Three SQS-chained Lambdas process an uploaded file, each triggered by the previous one's output queue:

1. **dispatcher** (`src/dispatcher/handler.py`) — triggered by S3 `ObjectCreated` events delivered via the dispatch-queue. Splits the file into a fixed **10-way byte-range partition fan-out** (`common/chunking.compute_partitions`, bounds worst-case concurrency regardless of file size), creates the `file_status` DynamoDB record, and sends one `WorkerMessage` per partition to the worker-queue. For `.txt` uploads, file size comes straight off the S3 event and dispatcher never calls `HeadObject`/`GetObject`. `.pdf` uploads are the exception: dispatcher downloads the whole PDF, extracts its text (`common/pdf.extract_pdf_text`, pypdf-based, pure Python), and re-uploads the extracted plain text to `extracted/{file_id}.extracted` in the same bucket — `.extracted`, never `.txt`/`.pdf`, since either suffix would re-trigger the same S3 notification and double-dispatch the extraction output as a bogus independent upload. Partitioning then runs against that extracted text's byte length instead of the PDF's own size; `file_id`/`file_status.file_size_bytes` still reflect the original PDF throughout (chunk IDs, status display), so **worker and db_writer need no PDF-specific logic at all**. Permanently-invalid input (unsupported extension, empty file, oversized or textless PDF, checked in `_resolve_worker_object`) raises `DispatchValidationError`, which the handler catches to write a `status=failed` `file_status` record via `create_failed_file_status_record` instead of letting the exception propagate — these are non-retryable, so SQS's normal retry/DLQ handling (appropriate for transient errors) would otherwise just retry a doomed message forever while the file stays invisible to the frontend.
2. **worker** (`src/worker/handler.py`) — one invocation per partition. Fetches its byte range (plus a small overlap buffer for non-final partitions) from S3, trims to clean whitespace boundaries (`common/chunking.trim_partition_bytes` — this is what keeps chunks from splitting mid-word across partition seams), splits into overlapping token-window chunks (`chunk_text`), embeds each via Bedrock Titan Text Embeddings V2 (`common/bedrock.embed_batch` — sequential `invoke_model` calls; there's no true multi-text batch API for synchronous invoke), and sends one `WriteMessage` per chunk to the write-queue.
3. **db_writer** (`src/db_writer/handler.py`) — batched (`batch_size=10`). Upserts each chunk into Aurora's `chunks` table (`common/vectordb.VectorDBClient`) and is the **only** thing that advances `file_status` (`chunks_written`, `completed_workers`, final `status=indexed`). This is the only Lambda that touches the `chunk_completion` table.

**Idempotency is load-bearing throughout**, because SQS is at-least-once and every step must survive redelivery without double-counting or duplicating work:
- `chunk_id` is deterministic (`file_id_w{worker_index}_c{local_chunk_num}`, `common/chunking.make_chunk_id`), so a redelivered worker message reproduces identical chunk ids rather than new ones.
- `create_file_status_record` and `mark_chunk_completion` (`common/db.py`) are both conditional puts (`attribute_not_exists`) — the caller treats a failed condition as "already done, skip the rest of this handler," not an error.
- Completion tracking is per-partition, not per-chunk-index, because a partition's chunk count varies and SQS doesn't guarantee order: `worker` stamps `chunk_count`/`total_workers` into each chunk's metadata, and `db_writer` maintains a `chunks_written` map (`{worker_index: count}`) on the `file_status` item, only rolling up to `completed_workers`/`status=indexed` once a partition's count reaches its `chunk_count`.

### Dual-backend toggles (production vs. local emulator)

Two independent env-var toggles exist because AWS Bedrock and the RDS Data API have no local emulators:
- `EMBEDDINGS_PROVIDER=bedrock|fake` (`common/bedrock.py`) — `fake` returns deterministic hash-seeded vectors (retrieval-meaningless, but free/offline). Local docker-compose sets this on `worker`.
- `DB_MODE=data_api|psycopg` (`common/vectordb.py`, mirrored in `frontend/lib/vectordb.ts`) — production Aurora is reached via the RDS Data API (no VPC needed by any Lambda); local docker-compose's plain `pgvector/pgvector` container is reached via `psycopg`/`pg` over the normal wire protocol instead. Both backends run the same SQL against the same `chunks` table schema, which lives in **two places that must stay in sync**: `infra/local/postgres-init/002-schema.sql` and a `null_resource` in `infra/cloud/modules/aurora/main.tf`.

### Frontend (`frontend/`)

Next.js App Router API routes (`frontend/app/api/*`) call `@aws-sdk/*` directly — no Lambda in the request path for upload/status/files. `lib/aws-clients.ts` mirrors the Python side's env-driven, endpoint-override-aware client construction. One asymmetry to know about: the S3 client used for presigning upload URLs (`app/api/upload/route.ts`) must use a **browser-reachable** endpoint (`AWS_ENDPOINT_URL_S3_PUBLIC`) since the browser PUTs directly against the presigned URL, while every other AWS client (DynamoDB, SQS, and the server-side S3 delete path) uses the docker-network-internal endpoint (`AWS_ENDPOINT_URL_S3`, etc.) — see the comments in `lib/aws-clients.ts` and `docker-compose.yml`'s `frontend` service before changing either.

`app/api/query/route.ts` does real retrieval, following the same no-Lambda-in-the-request-path pattern as upload/status/files: `lib/bedrock.ts` embeds the query via real Bedrock (TS port of `common/bedrock.py`'s `embed_text`, same `AWS_BEDROCK_*` credential handling), `lib/vectordb.ts`'s `similaritySearch` runs the pgvector cosine-similarity query (same `DB_MODE` toggle as the Python side), and `lib/generation.ts` generates the answer via the Anthropic SDK directly using `ANTHROPIC_API_KEY` (not Bedrock — a deliberate LLM-agnostic split between the embedding model and the generation model). There is no `query_api` Lambda; retrieval never needed one. `lib/api.ts`'s `queryApi()` signature didn't need to change.

### Infra layout

`infra/` splits into `infra/cloud/` (Terraform: S3, SQS, DynamoDB, Aurora Serverless v2/pgvector, IAM, Lambda zip+layer packaging — one shared `common/` Lambda Layer, since dispatcher/worker/db_writer need zero third-party deps beyond the runtime-provided `boto3`, plus a second layer just for dispatcher's `pypdf` dependency built via a `null_resource`/`local-exec` `pip install --target` — pypdf is pure Python, so no cross-compilation concern for Lambda's target architecture) and `infra/local/` (docker-compose emulator stack: MinIO/ElasticMQ/dynamodb-local/pgvector-Postgres, plus `scripts/run_*.py` wrappers that long-poll an SQS queue and invoke the real unmodified Lambda `handler(event, context)` — no code duplication between local and deployed Lambda targets). Terraform conventions (backend/bootstrap/module layout, tagging, IAM-per-Lambda) mirror the user's other boilerplate repos rather than being invented fresh.

## Current state / not yet built

Per the original design doc, this is a mid-build system. Built so far: `dispatcher`, `worker`, `db_writer`, the full upload/status frontend flow, real query/retrieval (Bedrock embedding + pgvector search + Claude generation, all directly in the Next.js API route, no Lambda), `.txt` and `.pdf` ingestion (S3 notification filters on both suffixes; see the dispatcher entry above for the PDF text-extraction detour), and the local dev stack. **Not yet built:** OCR/scanned-PDF support (text-layer extraction only — `common/pdf.extract_pdf_text` returns empty for image-only PDFs, which dispatcher now surfaces as a `status=failed` file rather than an uncaught exception), encrypted-PDF support, the `redrive` Lambda, API Gateway/EventBridge/SNS, GitHub Actions CI, and a full structured-logging/metrics pass. **Known gap:** a partition whose trimmed text is empty or whitespace-only (possible for very small files under the fixed 10-way fan-out, or a partition landing entirely in a run of blank lines) makes `worker` send zero `WriteMessage`s for that partition; `db_writer`'s per-partition completion tracking never learns that partition existed, so the file is stuck at `status=processing` forever with no error. A real fix needs a protocol change (worker signaling "partition done, zero chunks" some way db_writer can count) — flagged, not yet fixed. Similarly, `db_writer`'s idempotency marker (`mark_chunk_completion`) is written *before* the `chunks_written`/`completed_workers`/`mark_file_indexed` cascade it gates, so a crash in that narrow window permanently undercounts a partition rather than retrying cleanly on redelivery — a real fix likely needs those writes combined into one DynamoDB transaction. Both are edge cases, not observed in normal use, but worth fixing before relying on this for anything beyond personal-scale testing. When picking this up, don't assume any of these exist without checking current source — this list reflects the last known state, not a guarantee.
