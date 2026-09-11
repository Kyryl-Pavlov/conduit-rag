output "dispatcher_role_arn" {
  value = aws_iam_role.dispatcher.arn
}

output "worker_role_arn" {
  value = aws_iam_role.worker.arn
}

output "db_writer_role_arn" {
  value = aws_iam_role.db_writer.arn
}

output "transcribe_completion_role_arn" {
  value = aws_iam_role.transcribe_completion.arn
}

output "video_completion_role_arn" {
  value = aws_iam_role.video_completion.arn
}

output "dispatcher_role_name" {
  # Needed at root (infra/cloud/main.tf) to attach the
  # states:StartExecution grant without creating a module cycle -- see the
  # comment on aws_iam_role_policy.dispatcher above.
  value = aws_iam_role.dispatcher.name
}
