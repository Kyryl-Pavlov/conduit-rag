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

variable "transcribe_completion_role_arn" {
  type = string
}

variable "transcribe_completion_timeout_seconds" {
  type = number
}

variable "aws_region" {
  # Needed to hand-construct the video-analysis state machine's ARN (see
  # local.video_state_machine_arn) rather than reference video_pipeline's
  # output directly -- referencing it would create a module cycle, since
  # video_pipeline itself depends on this module's video_completion function
  # ARN.
  type = string
}

variable "video_completion_role_arn" {
  type = string
}

variable "video_completion_timeout_seconds" {
  type = number
}

variable "video_provider" {
  type = string
}
