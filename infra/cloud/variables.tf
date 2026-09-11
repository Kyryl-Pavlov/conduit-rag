variable "app_name" {
  description = "Application name used as a prefix for all resources."
  type        = string
  default     = "conduit-rag"
}

variable "environment" {
  description = "Deployment environment."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be 'dev' or 'prod'."
  }
}

variable "aws_region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "allowed_upload_origins" {
  description = "Origins allowed to PUT directly to presigned upload URLs. Defaults to the local Next.js dev server, since the frontend runs locally against this deployed backend."
  type        = list(string)
  default     = ["http://localhost:3000"]
}

# ── SQS ──────────────────────────────────────────────────────────────────────

variable "dispatcher_timeout_seconds" {
  description = "Dispatcher Lambda timeout. 90s (up from 30s) to cover the .pdf path's synchronous download+extract+re-upload."
  type        = number
  default     = 90
}

variable "worker_timeout_seconds" {
  description = "Worker Lambda timeout."
  type        = number
  default     = 120
}

variable "db_writer_timeout_seconds" {
  description = "db_writer Lambda timeout."
  type        = number
  default     = 60
}

variable "transcribe_completion_timeout_seconds" {
  description = "transcribe_completion Lambda timeout."
  type        = number
  default     = 60
}

variable "video_completion_timeout_seconds" {
  description = "video_completion Lambda timeout."
  type        = number
  default     = 60
}

# ── Video pipeline ───────────────────────────────────────────────────────────

variable "video_task_cpu" {
  description = "Fargate video-analysis task CPU units (1024 = 1 vCPU)."
  type        = string
  default     = "1024"
}

variable "video_task_memory" {
  description = "Fargate video-analysis task memory, in MB."
  type        = string
  default     = "2048"
}

variable "video_analysis_timeout_seconds" {
  description = "Step Functions video-analysis Task state timeout -- a safety net, not a real operational ceiling (a real vision-LLM call's actual duration is unknown until one exists)."
  type        = number
  default     = 3600
}

variable "video_provider" {
  description = "VIDEO_PROVIDER for dispatcher/the video-analysis Fargate task. Deliberately NOT 'fake' by default -- unlike the other dual-backend toggles, no real provider is implemented yet, so a real terraform apply should surface a deliberate NotImplementedError rather than silently behave like local dev."
  type        = string
  default     = "unimplemented"
}

# ── Aurora ───────────────────────────────────────────────────────────────────

variable "aurora_min_capacity" {
  description = "Aurora Serverless v2 minimum ACUs (0.5 is the lowest available; the cluster still incurs a small baseline cost when > 0 but scales storage/compute down aggressively when idle)."
  type        = number
  default     = 0.5
}

variable "aurora_max_capacity" {
  description = "Aurora Serverless v2 maximum ACUs."
  type        = number
  default     = 2
}

variable "aurora_engine_version" {
  description = "Aurora PostgreSQL engine version (must support the pgvector extension)."
  type        = string
  default     = "16.6"
}
