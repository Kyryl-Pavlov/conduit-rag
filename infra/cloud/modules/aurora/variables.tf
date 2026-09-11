variable "app_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "engine_version" {
  description = "Aurora PostgreSQL engine version. Confirm pgvector support for this version with `aws rds describe-db-engine-versions --engine aurora-postgresql` before applying."
  type        = string
}

variable "min_capacity" {
  type = number
}

variable "max_capacity" {
  type = number
}
