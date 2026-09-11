---
name: terraform-and-aws
description: Use when writing or reviewing Terraform under infra/cloud, or reasoning about this project's AWS architecture (Lambda, SQS, DynamoDB, Aurora/pgvector, S3, IAM, EventBridge) — module layout, naming, IAM/layer conventions, and what's safe to run (validate/plan only, no apply).
---

# Conduit RAG — Terraform and AWS conventions

Applies to `infra/cloud/` (Terraform, deployed AWS) and the AWS-shaped decisions that also touch `infra/local/` (docker-compose emulators). Read the root `CLAUDE.md`'s Architecture and Commands sections first — this skill goes one level deeper on *how* the Terraform itself is structured.

## Repo layout and safety

- `infra/cloud/` = real AWS via Terraform. `infra/cloud/bootstrap/` (S3 tfstate bucket + DynamoDB lock table) is separate and one-time-applied — never fold it into the root module or its plan.
- **No `terraform apply` has ever been run against the root module.** Treat it as author/validate/plan-only: `terraform validate`, `terraform fmt -recursive`, `terraform plan -var-file=dev.tfvars` are all safe to run freely; never run `terraform apply` without the user explicitly asking, even if a plan looks clean.
- `backend.hcl` and `dev.tfvars` are gitignored, copied from `backend.hcl.example` / `dev.tfvars.example` — don't commit real values, and don't assume they exist in a fresh checkout.
- Root provider block (`infra/cloud/versions.tf`): AWS provider `~> 5.0`, `archive ~> 2.4`, `null ~> 3.2`, `required_version >= 1.6`, S3 partial backend, `default_tags` = `{Project, Environment, ManagedBy=terraform}` applied to everything automatically — don't hand-add those same tags on individual resources.

## Module layout

One module per AWS concern under `infra/cloud/modules/`: `s3`, `sqs`, `dynamodb`, `aurora`, `iam`, `lambda`. The root `main.tf` wires them together by passing ARNs/outputs from one module into the next (`module.sqs.dispatch_queue_arn` → `module.iam`, `module.iam.*_role_arn` → `module.lambda`, etc.). When adding a new AWS resource, put it in the matching existing module rather than inventing a new one unless it's a genuinely new concern.

**Naming**: every module uses `locals { prefix = "${var.app_name}-${var.environment}" }`, and every resource name is `"${local.prefix}-<thing>"`. Match this exactly for new resources.

## Adding a new Lambda: the established pattern

Each function in `modules/lambda/main.tf` + `modules/iam/main.tf` follows the same shape — replicate all of it for a new function, not just the parts that seem necessary:

1. **Layers**: one shared `common` layer (zipped from `src/common/*.py` via `archive_file` + a `fileset` dynamic block, laid out as `python/common/...` so `import common.x` resolves). Only add a *new* layer if the function needs a third-party dependency nothing else uses (see the `pypdf` layer, built via `null_resource`/`local-exec` `pip install --target` — safe without cross-compilation concern only because it's pure Python).
2. **Archive**: `data "archive_file"` zipping just that function's `handler.py` (no per-function pip install unless it needs its own layer).
3. **Log group**: an explicit `aws_cloudwatch_log_group` (`retention_in_days = 14`) with `depends_on` on the function, so the function never falls back to an unmanaged, no-retention auto-created group.
4. **Function resource**: `memory_size` and `timeout` sized for what the function actually does synchronously (dispatcher's PDF path got bumped to 512MB/longer timeout specifically because of the in-memory download+parse+reupload — comment *why*, don't just pick a number).
5. **Trigger**: `aws_lambda_event_source_mapping` for SQS-polled functions (`batch_size`, plus `maximum_batching_window_in_seconds` if batching matters, as with `db_writer`). For a push-style trigger (EventBridge, not a poll source), use `aws_cloudwatch_event_rule` + `aws_cloudwatch_event_target` + `aws_lambda_permission`, **and** add `aws_lambda_function_event_invoke_config` to make async-invoke retry explicit — an EventBridge-triggered Lambda gets none of SQS's visibility-timeout retry for free, so this is not optional boilerplate.
6. **IAM**: one dedicated role + one inline policy per function (never a shared role across functions), `AWSLambdaBasicExecutionRole` attached, then a statement-per-permission inline policy with a `Sid` and a comment explaining *why* — especially for any `Resource = "*"`, which should always be justified (e.g. "can't scope to a not-yet-existing job ARN") rather than a lazy default. Flag anything not yet verified against real AWS with an `ASSUMPTION, verify against real AWS:` comment, and don't silently resolve/remove one you find without new evidence.

## Cross-cutting things to keep in sync

- The `chunks` table schema lives in **two places** that must stay identical: `infra/local/postgres-init/002-schema.sql` and the `null_resource` in `infra/cloud/modules/aurora/main.tf`. Any schema change touches both.
- Any new AWS dependency with no local emulator (Bedrock, RDS Data API, Transcribe today) needs a paired env-var provider toggle (`*_PROVIDER` or `*_MODE`) so `infra/local`'s docker-compose stack keeps working offline — this is a Terraform *and* Python concern together (see [[python-be]]).

## Procedure for infra changes

1. Identify which existing module the change belongs in (rarely a new one).
2. Mirror the role/policy/layer/log-group/trigger pattern above exactly for a new Lambda.
3. `terraform fmt -recursive && terraform validate`, then `terraform plan -var-file=dev.tfvars` to sanity-check — report the plan, don't apply it.
4. If the change also affects the DB schema or a provider toggle, update the paired file/module too (see above).

See [[multimodal-rag]] for the full worked example (audio transcription: `transcribe_completion` module additions) of onboarding a new async, EventBridge-triggered Lambda end to end.
