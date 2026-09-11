locals {
  prefix = "${var.app_name}-${var.environment}"
}

# ── dispatch-queue (S3 event notifications -> dispatcher) ────────────────────

resource "aws_sqs_queue" "dispatch_dlq" {
  name                      = "${local.prefix}-dispatch-dlq"
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "dispatch" {
  name                       = "${local.prefix}-dispatch-queue"
  visibility_timeout_seconds = var.dispatcher_timeout_seconds * 6 # AWS guidance: >= 6x consumer timeout
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dispatch_dlq.arn
    maxReceiveCount     = 3
  })
}

# ── worker-queue (dispatcher -> worker) ───────────────────────────────────────

resource "aws_sqs_queue" "worker_dlq" {
  name                      = "${local.prefix}-worker-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "worker" {
  name                       = "${local.prefix}-worker-queue"
  visibility_timeout_seconds = var.worker_timeout_seconds * 6
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.worker_dlq.arn
    maxReceiveCount     = 3
  })
}

# ── write-queue (worker -> db_writer) ─────────────────────────────────────────

resource "aws_sqs_queue" "write_dlq" {
  name                      = "${local.prefix}-write-dlq"
  message_retention_seconds = 1209600
  sqs_managed_sse_enabled   = true
}

resource "aws_sqs_queue" "write" {
  name                       = "${local.prefix}-write-queue"
  visibility_timeout_seconds = var.db_writer_timeout_seconds * 6 # AWS guidance: >= 6x consumer timeout
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.write_dlq.arn
    maxReceiveCount     = 3
  })
}
