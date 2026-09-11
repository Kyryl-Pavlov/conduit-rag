variable "app_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "dispatcher_role_arn" {
  type = string
}

variable "worker_role_arn" {
  type = string
}

variable "dispatch_queue_arn" {
  type = string
}

variable "worker_queue_arn" {
  type = string
}

variable "worker_queue_url" {
  type = string
}

variable "write_queue_url" {
  type = string
}

variable "file_status_table_name" {
  type = string
}

variable "dispatcher_timeout_seconds" {
  type = number
}

variable "worker_timeout_seconds" {
  type = number
}

variable "db_writer_role_arn" {
  type = string
}

variable "write_queue_arn" {
  type = string
}

variable "chunk_completion_table_name" {
  type = string
}

variable "aurora_cluster_arn" {
  type = string
}

variable "aurora_secret_arn" {
  type = string
}

variable "aurora_database_name" {
  type = string
}

variable "db_writer_timeout_seconds" {
  type = number
}
