module "s3" {
  source = "./modules/s3"

  app_name               = var.app_name
  environment            = var.environment
  allowed_upload_origins = var.allowed_upload_origins
}

module "sqs" {
  source = "./modules/sqs"

  app_name                   = var.app_name
  environment                = var.environment
  dispatcher_timeout_seconds = var.dispatcher_timeout_seconds
  worker_timeout_seconds     = var.worker_timeout_seconds
  db_writer_timeout_seconds  = var.db_writer_timeout_seconds
}

module "dynamodb" {
  source = "./modules/dynamodb"

  app_name    = var.app_name
  environment = var.environment
}

module "aurora" {
  source = "./modules/aurora"

  app_name       = var.app_name
  environment    = var.environment
  aws_region     = var.aws_region
  engine_version = var.aurora_engine_version
  min_capacity   = var.aurora_min_capacity
  max_capacity   = var.aurora_max_capacity
}

module "iam" {
  source = "./modules/iam"

  app_name                   = var.app_name
  environment                = var.environment
  aws_region                 = var.aws_region
  dispatch_queue_arn         = module.sqs.dispatch_queue_arn
  worker_queue_arn           = module.sqs.worker_queue_arn
  write_queue_arn            = module.sqs.write_queue_arn
  file_status_table_arn      = module.dynamodb.file_status_table_arn
  upload_bucket_arn          = module.s3.bucket_arn
  chunk_completion_table_arn = module.dynamodb.chunk_completion_table_arn
  aurora_cluster_arn         = module.aurora.cluster_arn
  aurora_secret_arn          = module.aurora.master_user_secret_arn
}

module "lambda" {
  source = "./modules/lambda"

  app_name                              = var.app_name
  environment                           = var.environment
  dispatcher_role_arn                   = module.iam.dispatcher_role_arn
  worker_role_arn                       = module.iam.worker_role_arn
  dispatch_queue_arn                    = module.sqs.dispatch_queue_arn
  worker_queue_arn                      = module.sqs.worker_queue_arn
  worker_queue_url                      = module.sqs.worker_queue_url
  write_queue_url                       = module.sqs.write_queue_url
  file_status_table_name                = module.dynamodb.file_status_table_name
  dispatcher_timeout_seconds            = var.dispatcher_timeout_seconds
  worker_timeout_seconds                = var.worker_timeout_seconds
  db_writer_role_arn                    = module.iam.db_writer_role_arn
  write_queue_arn                       = module.sqs.write_queue_arn
  chunk_completion_table_name           = module.dynamodb.chunk_completion_table_name
  aurora_cluster_arn                    = module.aurora.cluster_arn
  aurora_secret_arn                     = module.aurora.master_user_secret_arn
  aurora_database_name                  = module.aurora.database_name
  db_writer_timeout_seconds             = var.db_writer_timeout_seconds
  transcribe_completion_role_arn        = module.iam.transcribe_completion_role_arn
  transcribe_completion_timeout_seconds = var.transcribe_completion_timeout_seconds
  aws_region                            = var.aws_region
  video_completion_role_arn             = module.iam.video_completion_role_arn
  video_completion_timeout_seconds      = var.video_completion_timeout_seconds
  video_provider                        = var.video_provider
}

module "video_pipeline" {
  source = "./modules/video_pipeline"

  app_name                       = var.app_name
  environment                    = var.environment
  aws_region                     = var.aws_region
  upload_bucket_arn              = module.s3.bucket_arn
  video_completion_function_arn  = module.lambda.video_completion_function_arn
  video_task_cpu                 = var.video_task_cpu
  video_task_memory              = var.video_task_memory
  video_analysis_timeout_seconds = var.video_analysis_timeout_seconds
  video_provider                 = var.video_provider
}

# dispatcher's states:StartExecution grant lives at root (not inside the iam
# module) to avoid a module-ordering cycle: iam would need
# video_pipeline.state_machine_arn, which needs lambda's video_completion
# function ARN, which needs iam's video_completion role ARN. Mirrors
# aws_sqs_queue_policy.dispatch_from_s3 below, the existing precedent for
# this exact "lives at root to dodge a cycle" pattern. Unlike Transcribe's
# necessarily-unscoped StartTranscriptionJob grant, this one CAN be
# ARN-scoped to the specific state machine.
resource "aws_iam_role_policy" "dispatcher_start_video_execution" {
  name = "start-video-execution"
  role = module.iam.dispatcher_role_name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "StartVideoAnalysisExecution"
      Effect   = "Allow"
      Action   = ["states:StartExecution"]
      Resource = module.video_pipeline.state_machine_arn
    }]
  })
}

# ── S3 -> SQS glue ─────────────────────────────────────────────────────────
# Lives at root (not inside either module) to avoid a module-ordering
# dependency between s3 and sqs.

resource "aws_sqs_queue_policy" "dispatch_from_s3" {
  queue_url = module.sqs.dispatch_queue_url

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "AllowS3SendMessage"
      Effect    = "Allow"
      Principal = { Service = "s3.amazonaws.com" }
      Action    = "sqs:SendMessage"
      Resource  = module.sqs.dispatch_queue_arn
      Condition = {
        ArnEquals = { "aws:SourceArn" = module.s3.bucket_arn }
      }
    }]
  })
}

# .txt, .pdf, audio (see common.transcription.AUDIO_EXTENSIONS), and video
# (see common.video.VIDEO_EXTENSIONS) are the only supported upload types --
# dispatcher/handler.py raises on anything else. IMPORTANT: dispatcher (and
# transcribe_completion/video_completion, for the real audio/video paths)
# stage extracted text/transcripts/detections back into this same bucket
# under `extracted/{file_id}.extracted`, and Transcribe itself writes job
# output under `transcribe-output/*.json` -- neither prefix's suffix may ever
# become one of the filter_suffix values below, or that output would
# re-trigger this notification and get double-dispatched as a bogus
# independent upload (currently safe: none of the nine suffixes is ".json").
resource "aws_s3_bucket_notification" "uploads" {
  bucket = module.s3.bucket_name

  queue {
    queue_arn     = module.sqs.dispatch_queue_arn
    events        = ["s3:ObjectCreated:*"]
    filter_suffix = ".txt"
  }

  queue {
    queue_arn     = module.sqs.dispatch_queue_arn
    events        = ["s3:ObjectCreated:*"]
    filter_suffix = ".pdf"
  }

  queue {
    queue_arn     = module.sqs.dispatch_queue_arn
    events        = ["s3:ObjectCreated:*"]
    filter_suffix = ".mp3"
  }

  queue {
    queue_arn     = module.sqs.dispatch_queue_arn
    events        = ["s3:ObjectCreated:*"]
    filter_suffix = ".wav"
  }

  queue {
    queue_arn     = module.sqs.dispatch_queue_arn
    events        = ["s3:ObjectCreated:*"]
    filter_suffix = ".m4a"
  }

  queue {
    queue_arn     = module.sqs.dispatch_queue_arn
    events        = ["s3:ObjectCreated:*"]
    filter_suffix = ".flac"
  }

  queue {
    queue_arn     = module.sqs.dispatch_queue_arn
    events        = ["s3:ObjectCreated:*"]
    filter_suffix = ".mp4"
  }

  queue {
    queue_arn     = module.sqs.dispatch_queue_arn
    events        = ["s3:ObjectCreated:*"]
    filter_suffix = ".mov"
  }

  queue {
    queue_arn     = module.sqs.dispatch_queue_arn
    events        = ["s3:ObjectCreated:*"]
    filter_suffix = ".webm"
  }

  depends_on = [aws_sqs_queue_policy.dispatch_from_s3]
}
