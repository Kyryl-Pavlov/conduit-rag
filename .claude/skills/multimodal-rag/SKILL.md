---
name: multimodal-rag
description: Use when adding, reviewing, or reasoning about ingestion of a new file modality (audio, image, video, structured data, etc.) into the RAG pipeline, or when discussing multimodal retrieval/embedding strategy generally. Grounded in this repo's existing .txt/.pdf/audio-transcription precedents.
---

# Multimodal RAG — onboarding a new modality into this pipeline

This is a general reference for multimodal ingestion and retrieval, anchored in the concrete pattern this repo already uses three times over (`.txt` flat prose → `.pdf` text extraction → audio transcription). Read the root `CLAUDE.md`'s pipeline section first; this skill is the "how to add a fourth" playbook plus broader multimodal-RAG considerations.

## The pattern this repo has converged on

Every modality this pipeline ingests gets normalized to **staged, partitionable bytes at a well-known S3 key** before worker/db_writer ever see it — that's the single idea that keeps the two heaviest Lambdas (`worker`, `db_writer`) permanently modality-agnostic. Concretely:

1. **Normalize to a staged intermediate object.** PDF → extracted plain text at `extracted/{file_id}.extracted`. Audio → a JSONL transcript (one `{"start","end","text"}` segment per line) staged at the same `extracted/{file_id}.extracted` path. Whatever the source format, dispatcher (or its completion Lambda) produces one flat, byte-range-partitionable object. **Never reuse a suffix an S3 notification already watches** (`.txt`, `.pdf`, the raw audio extensions) for the staged object — that double-triggers dispatch and treats your own output as a bogus new upload.
2. **Decide sync vs. async extraction up front.** If extraction finishes within one Lambda invocation (pypdf's in-process parse), do it synchronously inside `dispatcher` and call `common.fanout.fan_out_partitions` directly — see `_resolve_worker_object`/`_extract_and_stage_pdf_text` in `dispatcher/handler.py`. If it's an external job that can run minutes (AWS Transcribe), `dispatcher` only **starts** the job and returns immediately — no `file_status` record is created yet, so the file is invisible in the frontend for the duration, which is an accepted, documented tradeoff, not a bug to silently fix with a placeholder status. A second Lambda triggered by the job's completion signal (EventBridge for Transcribe) does the staging + `fan_out_partitions` call instead — see `transcribe_completion/handler.py`. Because no DynamoDB record exists yet at job-start time to carry the upload's identity, **tag the external job** with `file_id`/`s3_bucket` at start time and recover them from the job's tags in the completion handler (`common.transcription.recover_upload_identity`) — this is the general answer to "how does the async completion handler know which upload this was."
3. **Add a `PartitionFormat` variant only if downstream trim/chunk logic must differ.** `PartitionFormat.TEXT` vs `PartitionFormat.TRANSCRIPT` in `common/models.py` is the discriminator `worker` branches on. If the new modality can be normalized to flat prose, reuse `TEXT` and skip this step entirely. If it needs its own trim/chunk semantics (e.g. structural boundaries that aren't whitespace), add a new enum value and a **sibling module** to `common/chunking.py` (see `common/transcript_chunking.py`) rather than branching inside the existing one — only pull in genuinely shared constants. `worker/handler.py` then gets one more `if is_<format>:` branch selecting trim/chunk functions; keep reading the discriminator with `.get(key, DEFAULT)` so old in-flight messages without the field still decode as the previous default.
4. **Provide a local/fake mode.** Any external service with no local emulator (Transcribe, Bedrock, an OCR/vision API) needs a `*_PROVIDER=real|fake` env toggle, mirroring `TRANSCRIPTION_PROVIDER`/`EMBEDDINGS_PROVIDER`. The fake branch must be deterministic (seed off `file_id`) and skip the async ceremony entirely if the real path is async — dispatcher's fake-audio branch calls the fake generator synchronously and fans out immediately in the same invocation, rather than pretending to poll a job that doesn't exist locally.
5. **IAM and Terraform follow [[terraform-and-aws]]'s per-Lambda pattern** — a new completion Lambda gets its own role, its own log group, and (if EventBridge-triggered) an explicit `aws_lambda_function_event_invoke_config` for retry, since it gets no SQS-visibility-timeout retry for free.
6. **worker and db_writer should need zero modality-specific code** beyond the trim/chunk branch in step 3. If you find yourself adding PDF- or audio-specific logic to `db_writer`, that's a signal the staging step (1) isn't normalizing far enough.

## Broader multimodal-RAG considerations beyond what's built here

- **Embeddings**: this repo currently embeds only text (Bedrock Titan Text Embeddings V2). A true cross-modal embedding (e.g. embedding an image directly, or a multimodal model) vs. a caption-then-embed-text approach (transcribe/caption first, embed the resulting text — which is exactly what the audio path already does) is a real design choice; caption-then-embed reuses the entire existing pipeline unchanged past staging, which is why it's the natural default here. A native multimodal embedding call would be a new `common/bedrock.py` function following the same provider-toggle pattern.
- **Chunk metadata should carry modality-specific provenance** the way the transcript path already does (`start_time`/`end_time` per chunk) — for images this might be a bounding box or page/region; for structured data, a row/column reference. Retrieval-time citation rendering (`frontend/components/SourceCitation.tsx`) should be able to use whatever provenance fields are present without requiring all chunks to share the same metadata shape.
- **Retrieval stays modality-agnostic by construction** here: `similaritySearch` in `frontend/lib/vectordb.ts` runs the same cosine-similarity query over the same `chunks` table regardless of source modality, because everything was normalized to a text embedding before it ever reached the DB. Keep it that way — don't special-case retrieval per modality; special-case ingestion instead.

## Known repo-wide gaps that also apply to any new modality

A partition whose extraction yields zero chunks leaves `db_writer`'s per-partition completion tracking permanently unaware that partition existed (file stuck at `processing` forever, no error) — see `CLAUDE.md`'s "Current state" section. A new modality's extraction step should be aware this failure mode exists and isn't yet fixed at the protocol level; don't assume it's been solved.

## Procedure for adding a new modality

1. Read `dispatcher/handler.py`'s PDF branch (sync precedent) and audio branch + `transcribe_completion/handler.py` (async precedent) end to end first.
2. Decide sync vs. async, and whether a new `PartitionFormat` is needed.
3. Implement staging (dispatcher or a new completion Lambda) + reuse `common.fanout.fan_out_partitions` unchanged.
4. Add the fake/local-dev provider branch and wire `infra/local`'s docker-compose to use it.
5. Add IAM/Terraform per [[terraform-and-aws]]; add unit tests for new pure trim/chunk logic and moto integration tests for the AWS-touching handler bits per [[python-be]].
6. Update `CLAUDE.md`'s pipeline description once the modality is real, including any new known gaps.
