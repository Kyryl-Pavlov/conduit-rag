locals {
  prefix = "${var.app_name}-${var.environment}"
}

# ── Dispatcher role ────────────────────────────────────────────────────────
# File size for .txt uploads comes from the S3 event notification payload, not
# a HeadObject call. The exception is .pdf uploads: dispatcher must download
# the PDF to extract its text (s3:GetObject) and re-upload the extracted text
# so workers can partition it like any other plain-text object (s3:PutObject,
# scoped to the extracted/* prefix it writes to -- see dispatcher/handler.py).
# Audio uploads are a second, structurally different exception: dispatcher
# only starts the (async) Transcribe job -- transcribe:StartTranscriptionJob
# can't be scoped to a not-yet-existing job resource ARN, hence Resource "*"
# on that one statement. Video uploads are a third: dispatcher starts a Step
# Functions execution instead (states:StartExecution) -- that grant lives at
# root in infra/cloud/main.tf, not here, to avoid a module-ordering cycle
# (iam would need video_pipeline's state machine ARN, which needs lambda's
# video_completion function ARN, which needs iam's video_completion role ARN).
# No chunk-completion table access: that table is owned entirely by db_writer.

resource "aws_iam_role" "dispatcher" {
  name = "${local.prefix}-dispatcher"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "dispatcher_basic_execution" {
  role       = aws_iam_role.dispatcher.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "dispatcher" {
  name = "app-permissions"
  role = aws_iam_role.dispatcher.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ConsumeDispatchQueue"
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = var.dispatch_queue_arn
      },
      {
        Sid      = "SendToWorkerQueue"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = var.worker_queue_arn
      },
      {
        Sid      = "FileStatusTableWrite"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = var.file_status_table_arn
      },
      {
        Sid      = "ReadUploadObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${var.upload_bucket_arn}/*"
      },
      {
        Sid      = "WriteExtractedText"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${var.upload_bucket_arn}/extracted/*"
      },
      {
        Sid      = "TranscribeStartJob"
        Effect   = "Allow"
        Action   = ["transcribe:StartTranscriptionJob"]
        Resource = "*"
      },
    ]
  })
}

# ── Worker role ────────────────────────────────────────────────────────────
# No DynamoDB permission at all: completion tracking is reserved for the
# future db_writer Lambda. No VPC execution role: this function never touches
# Aurora.

resource "aws_iam_role" "worker" {
  name = "${local.prefix}-worker"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "worker_basic_execution" {
  role       = aws_iam_role.worker.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "worker" {
  name = "app-permissions"
  role = aws_iam_role.worker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ConsumeWorkerQueue"
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = var.worker_queue_arn
      },
      {
        Sid      = "SendToWriteQueue"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = var.write_queue_arn
      },
      {
        Sid      = "ReadUploadObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${var.upload_bucket_arn}/*"
      },
      {
        Sid      = "InvokeEmbeddingModel"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel"]
        Resource = "arn:aws:bedrock:${var.aws_region}::foundation-model/amazon.titan-embed-text-v2:0"
      },
    ]
  })
}

# ── db_writer role ─────────────────────────────────────────────────────────
# No VPC execution role: Aurora is reached via the RDS Data API (IAM-authenticated
# HTTPS), not a classic in-VPC Postgres connection -- see infra/cloud/modules/aurora.

resource "aws_iam_role" "db_writer" {
  name = "${local.prefix}-db-writer"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "db_writer_basic_execution" {
  role       = aws_iam_role.db_writer.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "db_writer" {
  name = "app-permissions"
  role = aws_iam_role.db_writer.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ConsumeWriteQueue"
        Effect   = "Allow"
        Action   = ["sqs:ReceiveMessage", "sqs:DeleteMessage", "sqs:GetQueueAttributes"]
        Resource = var.write_queue_arn
      },
      {
        Sid      = "ChunkCompletionTableWrite"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = var.chunk_completion_table_arn
      },
      {
        Sid      = "FileStatusTableUpdate"
        Effect   = "Allow"
        Action   = ["dynamodb:UpdateItem"]
        Resource = var.file_status_table_arn
      },
      {
        Sid      = "AuroraDataApi"
        Effect   = "Allow"
        Action   = ["rds-data:ExecuteStatement", "rds-data:BatchExecuteStatement"]
        Resource = var.aurora_cluster_arn
      },
      {
        # Data API callers authenticate via a secret they have permission to
        # read -- required alongside rds-data:ExecuteStatement, not implied by it.
        Sid      = "AuroraDataApiSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = var.aurora_secret_arn
      },
    ]
  })
}

# ── transcribe_completion role ────────────────────────────────────────────
# Triggered by EventBridge (not SQS) once a Transcribe job dispatcher started
# reaches a terminal state. Needs the same file_status/worker-queue/extracted-
# text permissions dispatcher's own audio path would need if it could do this
# synchronously, plus read access to the Transcribe job itself (to recover
# the original file_id/s3_bucket from job tags, and the failure reason on a
# FAILED job).

resource "aws_iam_role" "transcribe_completion" {
  name = "${local.prefix}-transcribe-completion"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "transcribe_completion_basic_execution" {
  role       = aws_iam_role.transcribe_completion.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "transcribe_completion" {
  name = "app-permissions"
  role = aws_iam_role.transcribe_completion.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "SendToWorkerQueue"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = var.worker_queue_arn
      },
      {
        Sid      = "FileStatusTableWrite"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = var.file_status_table_arn
      },
      {
        # Covers both the head_object size lookup and reading Transcribe's
        # own output JSON under transcribe-output/* -- S3 has no separate
        # HeadObject IAM action, it's covered by s3:GetObject.
        Sid      = "ReadUploadObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${var.upload_bucket_arn}/*"
      },
      {
        Sid      = "WriteExtractedText"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${var.upload_bucket_arn}/extracted/*"
      },
      {
        # ASSUMPTION, verify against real AWS: whether these support
        # resource-level scoping to transcription-job/* or need "*".
        Sid      = "TranscribeReadJob"
        Effect   = "Allow"
        Action   = ["transcribe:GetTranscriptionJob", "transcribe:ListTagsForResource"]
        Resource = "*"
      },
    ]
  })
}

# ── video_completion role ─────────────────────────────────────────────────
# Invoked directly by the video-analysis Step Functions state machine (see
# infra/cloud/modules/video_pipeline), not EventBridge -- Step Functions
# already knows success/failure via its own Catch and passes file_id/
# s3_bucket straight through as the execution's JSON input, so unlike
# transcribe_completion there's no job-tags lookup and no *ReadJob-style
# statement needed here. No WriteExtractedText either: the Fargate task
# stages the JSONL result, not this Lambda.

resource "aws_iam_role" "video_completion" {
  name = "${local.prefix}-video-completion"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "video_completion_basic_execution" {
  role       = aws_iam_role.video_completion.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy" "video_completion" {
  name = "app-permissions"
  role = aws_iam_role.video_completion.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "SendToWorkerQueue"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage"]
        Resource = var.worker_queue_arn
      },
      {
        Sid      = "FileStatusTableWrite"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem"]
        Resource = var.file_status_table_arn
      },
      {
        # Covers both head_object size lookups (original upload + the staged
        # extracted/* object the Fargate task wrote) -- S3 has no separate
        # HeadObject IAM action, it's covered by s3:GetObject.
        Sid      = "ReadUploadObjects"
        Effect   = "Allow"
        Action   = ["s3:GetObject"]
        Resource = "${var.upload_bucket_arn}/*"
      },
    ]
  })
}
