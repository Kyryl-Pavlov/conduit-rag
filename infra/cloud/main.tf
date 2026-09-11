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

  app_name                    = var.app_name
  environment                 = var.environment
  dispatcher_role_arn         = module.iam.dispatcher_role_arn
  worker_role_arn             = module.iam.worker_role_arn
  dispatch_queue_arn          = module.sqs.dispatch_queue_arn
  worker_queue_arn            = module.sqs.worker_queue_arn
  worker_queue_url            = module.sqs.worker_queue_url
  write_queue_url             = module.sqs.write_queue_url
  file_status_table_name      = module.dynamodb.file_status_table_name
  dispatcher_timeout_seconds  = var.dispatcher_timeout_seconds
  worker_timeout_seconds      = var.worker_timeout_seconds
  db_writer_role_arn          = module.iam.db_writer_role_arn
  write_queue_arn             = module.sqs.write_queue_arn
  chunk_completion_table_name = module.dynamodb.chunk_completion_table_name
  aurora_cluster_arn          = module.aurora.cluster_arn
  aurora_secret_arn           = module.aurora.master_user_secret_arn
  aurora_database_name        = module.aurora.database_name
  db_writer_timeout_seconds   = var.db_writer_timeout_seconds
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

# .txt and .pdf are the only supported upload types -- dispatcher/handler.py
# raises on anything else. IMPORTANT: dispatcher stages PDF-extracted text
# back into this same bucket under `extracted/{file_id}.extracted` -- that
# suffix must never become ".txt" or ".pdf", or its own extraction output
# would re-trigger this notification and get double-dispatched as a bogus
# independent upload.
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

  depends_on = [aws_sqs_queue_policy.dispatch_from_s3]
}
