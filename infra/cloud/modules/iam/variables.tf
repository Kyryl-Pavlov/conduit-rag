variable "app_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "dispatch_queue_arn" {
  type = string
}

variable "worker_queue_arn" {
  type = string
}

variable "write_queue_arn" {
  type = string
}

variable "file_status_table_arn" {
  type = string
}

variable "upload_bucket_arn" {
  type = string
}

variable "chunk_completion_table_arn" {
  type = string
}

variable "aurora_cluster_arn" {
  type = string
}

variable "aurora_secret_arn" {
  type = string
}
