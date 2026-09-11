output "upload_bucket_name" {
  value = module.s3.bucket_name
}

output "dispatch_queue_url" {
  value = module.sqs.dispatch_queue_url
}

output "worker_queue_url" {
  value = module.sqs.worker_queue_url
}

output "write_queue_url" {
  value = module.sqs.write_queue_url
}

output "file_status_table_name" {
  value = module.dynamodb.file_status_table_name
}

output "chunk_completion_table_name" {
  value = module.dynamodb.chunk_completion_table_name
}

output "aurora_cluster_arn" {
  value = module.aurora.cluster_arn
}

output "aurora_master_user_secret_arn" {
  value = module.aurora.master_user_secret_arn
}

output "dispatcher_function_name" {
  value = module.lambda.dispatcher_function_name
}

output "worker_function_name" {
  value = module.lambda.worker_function_name
}

output "db_writer_function_name" {
  value = module.lambda.db_writer_function_name
}
