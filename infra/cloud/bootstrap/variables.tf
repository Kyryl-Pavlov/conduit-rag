variable "aws_region" {
  description = "AWS region to create the state backend in."
  type        = string
  default     = "us-east-1"
}

variable "bucket_name" {
  description = "Name of the S3 bucket that will hold Terraform state."
  type        = string
  default     = "kporg-conduit-rag-tfstate"
}

variable "lock_table_name" {
  description = "Name of the DynamoDB table used for Terraform state locking."
  type        = string
  default     = "kporg-conduit-rag-tfstate-lock"
}
