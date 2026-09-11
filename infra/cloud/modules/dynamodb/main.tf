locals {
  prefix = "${var.app_name}-${var.environment}"
}

resource "aws_dynamodb_table" "file_status" {
  name         = "${local.prefix}-file-status"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "file_id"

  attribute {
    name = "file_id"
    type = "S"
  }
}

# Written by the future db_writer Lambda (not this phase) after a confirmed
# Aurora upsert; read by the future redrive Lambda's false-failure check.
# Created now, empty, so downstream phases don't need a schema migration.
resource "aws_dynamodb_table" "chunk_completion" {
  name         = "${local.prefix}-chunk-completion"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "chunk_id"

  attribute {
    name = "chunk_id"
    type = "S"
  }
}
