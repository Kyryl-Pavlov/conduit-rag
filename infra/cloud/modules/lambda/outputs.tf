output "dispatcher_function_name" {
  value = aws_lambda_function.dispatcher.function_name
}

output "worker_function_name" {
  value = aws_lambda_function.worker.function_name
}

output "db_writer_function_name" {
  value = aws_lambda_function.db_writer.function_name
}
