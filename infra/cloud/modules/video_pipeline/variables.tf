variable "app_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "aws_region" {
  type = string
}

variable "upload_bucket_arn" {
  type = string
}

variable "video_completion_function_arn" {
  type = string
}

variable "video_task_cpu" {
  type = string
}

variable "video_task_memory" {
  type = string
}

variable "video_analysis_timeout_seconds" {
  type = number
}

variable "video_provider" {
  type = string
}
