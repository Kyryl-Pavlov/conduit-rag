output "dispatcher_function_name" {
  value = aws_lambda_function.dispatcher.function_name
}

output "worker_function_name" {
  value = aws_lambda_function.worker.function_name
}

output "db_writer_function_name" {
  value = aws_lambda_function.db_writer.function_name
}

output "transcribe_completion_function_name" {
  value = aws_lambda_function.transcribe_completion.function_name
}

output "video_completion_function_name" {
  value = aws_lambda_function.video_completion.function_name
}

output "video_completion_function_arn" {
  # video_pipeline needs the actual ARN (not just the name) to grant
  # lambda:InvokeFunction and set the Step Functions Task state's
  # FunctionName parameter.
  value = aws_lambda_function.video_completion.arn
}
