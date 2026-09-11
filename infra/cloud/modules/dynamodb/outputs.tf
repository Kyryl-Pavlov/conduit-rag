output "file_status_table_name" {
  value = aws_dynamodb_table.file_status.name
}

output "file_status_table_arn" {
  value = aws_dynamodb_table.file_status.arn
}

output "chunk_completion_table_name" {
  value = aws_dynamodb_table.chunk_completion.name
}

output "chunk_completion_table_arn" {
  value = aws_dynamodb_table.chunk_completion.arn
}
