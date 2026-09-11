output "bucket_name" {
  description = "Copy into infra/cloud/backend.hcl as `bucket`."
  value       = aws_s3_bucket.tfstate.bucket
}

output "lock_table_name" {
  description = "Copy into infra/cloud/backend.hcl as `dynamodb_table`."
  value       = aws_dynamodb_table.tfstate_lock.name
}

output "next_steps" {
  description = "What to do after `terraform apply` in this directory."
  value       = <<-EOT
    1. Copy infra/cloud/backend.hcl.example to infra/cloud/backend.hcl and fill in:
         bucket         = "${aws_s3_bucket.tfstate.bucket}"
         key            = "conduit-rag/terraform.tfstate"
         region         = "${var.aws_region}"
         dynamodb_table = "${aws_dynamodb_table.tfstate_lock.name}"
    2. cd .. (infra/cloud/) and run: terraform init -backend-config=backend.hcl
  EOT
}
