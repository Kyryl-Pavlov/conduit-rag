# Conduit RAG

An LLM-agnostic retrieval-augmented generation (RAG) system. Upload a text file through the web UI; a serverless pipeline partitions, embeds, and indexes it into a pgvector-backed store so it can later be retrieved for question answering.

## Status

This is a work in progress, built incrementally from a design doc. Currently working end-to-end: file upload, ingestion pipeline (partitioning → embedding → vector store write), and status tracking. **Query/retrieval is currently mocked** in the frontend — there is no real similarity search yet. PDF support, redrive-on-failure handling, and CI are not yet built.

## Architecture

```
                 ┌────────────┐
  Next.js UI ───▶│  S3 upload  │
  (presigned URL)└─────┬──────┘
                        │ ObjectCreated event
                        ▼
                 ┌─────────────┐   dispatch-queue (SQS)
                 │ dispatcher  │◀──────────────────────
                 └─────┬───────┘
                        │ fan out: up to 10 byte-range
                        │ partitions per file
                        ▼
                 ┌─────────────┐   worker-queue (SQS)
                 │   worker    │◀──────────────────────
                 └─────┬───────┘
                        │ chunk + embed (Bedrock Titan)
                        ▼
                 ┌─────────────┐   write-queue (SQS)
                 │  db_writer  │◀──────────────────────
                 └─────┬───────┘
                        ▼
              Aurora Postgres + pgvector
```

Each stage is idempotent, so SQS's at-least-once delivery can never duplicate work or double-count completion. `db_writer` is the only component that writes to the vector store and the only thing that advances a file's status to `indexed`. See [CLAUDE.md](CLAUDE.md) for the full architectural write-up.

**Components:**
- `frontend/` — Next.js (App Router) UI: upload, file list, indexing status, chat-style query box.
- `src/dispatcher`, `src/worker`, `src/db_writer` — the three pipeline Lambdas.
- `src/common` — shared code (chunking/partitioning, Bedrock embedding client, DynamoDB helpers, vector store client), packaged as a shared Lambda Layer.
- `infra/cloud` — Terraform for the real AWS deployment (S3, SQS, DynamoDB, Aurora Serverless v2/pgvector, IAM, Lambda).
- `infra/local` — docker-compose stack of local AWS emulators (MinIO, ElasticMQ, dynamodb-local, Postgres/pgvector) so the pipeline can be run and tested without touching real AWS or Bedrock.

## Running locally

Requires Docker.

```bash
cd infra/local
docker compose up -d --build
```

This starts the full stack, including the frontend at `http://localhost:3000`. Upload a `.txt` file through the UI and watch its status move from `processing` to `indexed`.

Alternatively, drive the pipeline directly without the frontend:

```bash
python infra/local/scripts/simulate_upload.py path/to/file.txt
```

Local embeddings are fake (deterministic, hash-seeded vectors) since Bedrock has no local emulator — fine for exercising the pipeline, not for meaningful retrieval.

## Development

### Python (Lambdas + shared code)

```bash
pip install -r requirements-dev.txt
pytest          # unit + moto-mocked integration tests
ruff check .    # lint
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

## Deploying

Cloud infrastructure is defined in `infra/cloud/` (Terraform). It has been `validate`d and `fmt`ted but never `apply`d against real AWS — treat it as a starting point to review, not a proven deployment, before running it against an account.

```bash
cd infra/cloud
cp backend.hcl.example backend.hcl   # fill in your tfstate bucket/lock table (see infra/cloud/bootstrap)
cp dev.tfvars.example dev.tfvars
terraform init -backend-config=backend.hcl
terraform plan -var-file=dev.tfvars
```

## License

MIT — see [LICENSE](LICENSE).
