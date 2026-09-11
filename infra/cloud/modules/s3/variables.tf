variable "app_name" {
  type = string
}

variable "environment" {
  type = string
}

variable "allowed_upload_origins" {
  description = "Origins allowed to PUT directly to presigned upload URLs (the browser, not the frontend server)."
  type        = list(string)
}
