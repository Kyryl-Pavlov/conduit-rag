output "dispatch_queue_arn" {
  value = aws_sqs_queue.dispatch.arn
}

output "dispatch_queue_url" {
  value = aws_sqs_queue.dispatch.id
}

output "worker_queue_arn" {
  value = aws_sqs_queue.worker.arn
}

output "worker_queue_url" {
  value = aws_sqs_queue.worker.id
}

output "write_queue_arn" {
  value = aws_sqs_queue.write.arn
}

output "write_queue_url" {
  value = aws_sqs_queue.write.id
}
