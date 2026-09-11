output "state_machine_arn" {
  value = aws_sfn_state_machine.video_analysis.arn
}

output "ecr_repository_url" {
  value = aws_ecr_repository.video_task.repository_url
}
