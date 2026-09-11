variable "app_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "dispatcher_timeout_seconds" {
  type = number
}

variable "worker_timeout_seconds" {
  type = number
}

variable "db_writer_timeout_seconds" {
  type = number
}
