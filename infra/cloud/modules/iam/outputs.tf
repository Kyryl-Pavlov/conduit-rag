output "dispatcher_role_arn" {
  value = aws_iam_role.dispatcher.arn
}

output "worker_role_arn" {
  value = aws_iam_role.worker.arn
}

output "db_writer_role_arn" {
  value = aws_iam_role.db_writer.arn
}
