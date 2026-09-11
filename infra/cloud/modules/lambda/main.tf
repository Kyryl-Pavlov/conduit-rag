locals {
  prefix   = "${var.app_name}-${var.environment}"
  src_root = "${path.module}/../../../../src"
  runtime  = "python3.12"
}

# ── Common layer ─────────────────────────────────────────────────────────────
# Built from src/common/*.py, shared by both functions below (and by
# db_writer/redrive/query_api in later phases) instead of duplicating it into
# every function's zip. `python/common/...` is the path layout Python Lambda
# layers require for `import common.x` to resolve at runtime.

data "archive_file" "common_layer" {
  type        = "zip"
  output_path = "${path.module}/build/common-layer.zip"

  dynamic "source" {
    for_each = fileset("${local.src_root}/common", "**/*.py")
    content {
      content  = file("${local.src_root}/common/${source.value}")
      filename = "python/common/${source.value}"
    }
  }
}

resource "aws_lambda_layer_version" "common" {
  layer_name          = "${local.prefix}-common"
  filename            = data.archive_file.common_layer.output_path
  source_code_hash    = data.archive_file.common_layer.output_base64sha256
  compatible_runtimes = [local.runtime]
}

# ── pypdf layer ──────────────────────────────────────────────────────────────
# Only dispatcher needs pypdf (PDF text extraction) -- kept out of the shared
# common layer so worker/db_writer's deployment stays free of a dependency
# they don't use. pypdf is pure Python (no compiled extensions), so a plain
# `pip install --target` produces a layer that works regardless of the host
# building it or Lambda's target architecture -- no cross-compilation concern.

locals {
  pypdf_version = "5.1.0"
}

resource "null_resource" "pypdf_layer_build" {
  triggers = {
    pypdf_version = local.pypdf_version
  }

  provisioner "local-exec" {
    command = "pip install pypdf==${local.pypdf_version} --target ${path.module}/build/pypdf-layer/python --upgrade --no-cache-dir"
  }
}

data "archive_file" "pypdf_layer" {
  type        = "zip"
  output_path = "${path.module}/build/pypdf-layer.zip"
  source_dir  = "${path.module}/build/pypdf-layer"
  depends_on  = [null_resource.pypdf_layer_build]
}

resource "aws_lambda_layer_version" "pypdf" {
  layer_name          = "${local.prefix}-pypdf"
  filename            = data.archive_file.pypdf_layer.output_path
  source_code_hash    = data.archive_file.pypdf_layer.output_base64sha256
  compatible_runtimes = [local.runtime]
}

# ── Dispatcher function ────────────────────────────────────────────────────
# Zero third-party dependencies beyond the runtime-provided boto3 for the
# .txt path, so a plain zip of handler.py is sufficient -- no pip-install
# build step for dispatcher's own code. The .pdf path's pypdf dependency is
# supplied by the dedicated layer above instead of vendoring it into this zip.

data "archive_file" "dispatcher" {
  type        = "zip"
  source_file = "${local.src_root}/dispatcher/handler.py"
  output_path = "${path.module}/build/dispatcher.zip"
}

resource "aws_cloudwatch_log_group" "dispatcher" {
  name              = "/aws/lambda/${local.prefix}-dispatcher"
  retention_in_days = 14
}

resource "aws_lambda_function" "dispatcher" {
  function_name = "${local.prefix}-dispatcher"
  role          = var.dispatcher_role_arn
  handler       = "handler.handler"
  runtime       = local.runtime
  timeout       = var.dispatcher_timeout_seconds
  # 512MB (bumped from 256MB) and the 90s default timeout (see variables.tf)
  # give headroom for the .pdf path's synchronous download+parse+re-upload --
  # a regression from "dispatcher work is bounded regardless of file size,"
  # but the extraction step can't proceed without the whole PDF in memory.
  # These are estimates pending empirical verification against a real PDF.
  memory_size      = 512
  filename         = data.archive_file.dispatcher.output_path
  source_code_hash = data.archive_file.dispatcher.output_base64sha256
  layers           = [aws_lambda_layer_version.common.arn, aws_lambda_layer_version.pypdf.arn]

  environment {
    variables = {
      WORKER_QUEUE_URL  = var.worker_queue_url
      FILE_STATUS_TABLE = var.file_status_table_name
      NUM_PARTITIONS    = "10"
      MAX_PDF_BYTES     = tostring(50 * 1024 * 1024)
    }
  }

  depends_on = [aws_cloudwatch_log_group.dispatcher]
}

resource "aws_lambda_event_source_mapping" "dispatch" {
  event_source_arn = var.dispatch_queue_arn
  function_name    = aws_lambda_function.dispatcher.arn
  batch_size       = 1
  enabled          = true
}

# ── Worker function ────────────────────────────────────────────────────────

data "archive_file" "worker" {
  type        = "zip"
  source_file = "${local.src_root}/worker/handler.py"
  output_path = "${path.module}/build/worker.zip"
}

resource "aws_cloudwatch_log_group" "worker" {
  name              = "/aws/lambda/${local.prefix}-worker"
  retention_in_days = 14
}

resource "aws_lambda_function" "worker" {
  function_name    = "${local.prefix}-worker"
  role             = var.worker_role_arn
  handler          = "handler.handler"
  runtime          = local.runtime
  timeout          = var.worker_timeout_seconds
  memory_size      = 512
  filename         = data.archive_file.worker.output_path
  source_code_hash = data.archive_file.worker.output_base64sha256
  layers           = [aws_lambda_layer_version.common.arn]

  environment {
    variables = {
      WRITE_QUEUE_URL         = var.write_queue_url
      BEDROCK_MODEL_ID        = "amazon.titan-embed-text-v2:0"
      BEDROCK_EMBEDDING_DIM   = "1024"
      TARGET_TOKENS_PER_CHUNK = "500"
      MAX_TOKENS_PER_CHUNK    = "8192"
      OVERLAP_TOKENS          = "50"
      OVERLAP_BUFFER_BYTES    = "512"
    }
  }

  depends_on = [aws_cloudwatch_log_group.worker]
}

resource "aws_lambda_event_source_mapping" "worker" {
  event_source_arn = var.worker_queue_arn
  function_name    = aws_lambda_function.worker.arn
  batch_size       = 1
  enabled          = true
}

# ── db_writer function ─────────────────────────────────────────────────────
# DB_MODE defaults to "data_api" (see common/vectordb.py) -- this function
# needs no extra dependencies beyond the runtime-provided boto3, same as
# dispatcher/worker. psycopg is a local-docker-only dependency, never bundled
# here.

data "archive_file" "db_writer" {
  type        = "zip"
  source_file = "${local.src_root}/db_writer/handler.py"
  output_path = "${path.module}/build/db_writer.zip"
}

resource "aws_cloudwatch_log_group" "db_writer" {
  name              = "/aws/lambda/${local.prefix}-db-writer"
  retention_in_days = 14
}

resource "aws_lambda_function" "db_writer" {
  function_name                  = "${local.prefix}-db-writer"
  role                           = var.db_writer_role_arn
  handler                        = "handler.handler"
  runtime                        = local.runtime
  timeout                        = var.db_writer_timeout_seconds
  memory_size                    = 256
  filename                       = data.archive_file.db_writer.output_path
  source_code_hash               = data.archive_file.db_writer.output_base64sha256
  layers                         = [aws_lambda_layer_version.common.arn]
  reserved_concurrent_executions = 2 # bounds concurrent Aurora connections/Data API calls

  environment {
    variables = {
      FILE_STATUS_TABLE      = var.file_status_table_name
      CHUNK_COMPLETION_TABLE = var.chunk_completion_table_name
      AURORA_CLUSTER_ARN     = var.aurora_cluster_arn
      AURORA_SECRET_ARN      = var.aurora_secret_arn
      AURORA_DATABASE_NAME   = var.aurora_database_name
    }
  }

  depends_on = [aws_cloudwatch_log_group.db_writer]
}

resource "aws_lambda_event_source_mapping" "db_writer" {
  event_source_arn                   = var.write_queue_arn
  function_name                      = aws_lambda_function.db_writer.arn
  batch_size                         = 10
  maximum_batching_window_in_seconds = 5
  enabled                            = true
}
