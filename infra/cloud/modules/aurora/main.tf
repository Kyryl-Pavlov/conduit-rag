locals {
  prefix = "${var.app_name}-${var.environment}"
}

# ── Networking ─────────────────────────────────────────────────────────────
# Uses the account's default VPC/subnets -- no custom VPC or NAT gateway.
# Nothing needs classic network access this phase: db_writer/query_api (next
# phase) are intended to reach Aurora via the RDS Data API (IAM-authenticated,
# no VPC needed for any Lambda in this pipeline). See the plan's Deviations
# section for the rationale and the caveat about pgvector's `vector` type
# needing a SQL-side cast under the Data API's typed JSON protocol.

data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_db_subnet_group" "main" {
  name       = "${local.prefix}-aurora"
  subnet_ids = data.aws_subnets.default.ids
}

# Zero ingress rules for now -- nothing connects over the classic network path
# this phase. Add rules additively when a VPC-attached consumer is introduced.
resource "aws_security_group" "aurora" {
  name        = "${local.prefix}-aurora"
  description = "Aurora cluster security group (no ingress this phase)"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ── Aurora Serverless v2 cluster ─────────────────────────────────────────────

resource "aws_rds_cluster" "main" {
  cluster_identifier     = "${local.prefix}-vectors"
  engine                 = "aurora-postgresql"
  engine_mode            = "provisioned"
  engine_version         = var.engine_version
  database_name          = "conduit_rag"
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.aurora.id]

  manage_master_user_password = true # AWS-managed rotating secret, not stored in tfstate
  enable_http_endpoint        = true # RDS Data API

  storage_encrypted   = true
  skip_final_snapshot = true  # dev-appropriate; revisit for a prod environment
  deletion_protection = false # dev-appropriate; revisit for a prod environment

  serverlessv2_scaling_configuration {
    min_capacity = var.min_capacity
    max_capacity = var.max_capacity
  }
}

resource "aws_rds_cluster_instance" "main" {
  cluster_identifier  = aws_rds_cluster.main.id
  instance_class      = "db.serverless"
  engine              = aws_rds_cluster.main.engine
  engine_version      = aws_rds_cluster.main.engine_version
  publicly_accessible = false
}

# ── pgvector bootstrap ────────────────────────────────────────────────────────
# A single CREATE EXTENSION statement doesn't justify a dedicated bootstrap
# Lambda; the RDS Data API lets the AWS CLI do this directly. Requires AWS CLI
# v2 on the machine running `terraform apply`. Re-runs only if the cluster is
# replaced.

resource "null_resource" "pgvector_extension" {
  triggers = {
    cluster_id = aws_rds_cluster.main.id
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws rds-data execute-statement \
        --resource-arn "${aws_rds_cluster.main.arn}" \
        --secret-arn "${aws_rds_cluster.main.master_user_secret[0].secret_arn}" \
        --database "${aws_rds_cluster.main.database_name}" \
        --sql "CREATE EXTENSION IF NOT EXISTS vector;" \
        --region "${var.aws_region}"
    EOT
  }

  depends_on = [aws_rds_cluster_instance.main]
}

# ── chunks table schema ──────────────────────────────────────────────────────
# Mirrors infra/local/postgres-init/002-schema.sql -- keep both in sync.
# Same rationale as the pgvector bootstrap above: not worth a dedicated
# migration Lambda for two DDL statements when the Data API/AWS CLI can run
# them directly. Re-runs only if the cluster is replaced.

resource "null_resource" "chunks_schema" {
  triggers = {
    cluster_id = aws_rds_cluster.main.id
  }

  provisioner "local-exec" {
    command = <<-EOT
      aws rds-data execute-statement \
        --resource-arn "${aws_rds_cluster.main.arn}" \
        --secret-arn "${aws_rds_cluster.main.master_user_secret[0].secret_arn}" \
        --database "${aws_rds_cluster.main.database_name}" \
        --sql "CREATE TABLE IF NOT EXISTS chunks (chunk_id TEXT PRIMARY KEY, file_id TEXT NOT NULL, text TEXT NOT NULL, embedding VECTOR(1024) NOT NULL, metadata JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ NOT NULL DEFAULT now());" \
        --region "${var.aws_region}" && \
      aws rds-data execute-statement \
        --resource-arn "${aws_rds_cluster.main.arn}" \
        --secret-arn "${aws_rds_cluster.main.master_user_secret[0].secret_arn}" \
        --database "${aws_rds_cluster.main.database_name}" \
        --sql "CREATE INDEX IF NOT EXISTS chunks_file_id_idx ON chunks (file_id);" \
        --region "${var.aws_region}" && \
      aws rds-data execute-statement \
        --resource-arn "${aws_rds_cluster.main.arn}" \
        --secret-arn "${aws_rds_cluster.main.master_user_secret[0].secret_arn}" \
        --database "${aws_rds_cluster.main.database_name}" \
        --sql "CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw_idx ON chunks USING hnsw (embedding vector_cosine_ops);" \
        --region "${var.aws_region}"
    EOT
  }

  depends_on = [null_resource.pgvector_extension]
}
