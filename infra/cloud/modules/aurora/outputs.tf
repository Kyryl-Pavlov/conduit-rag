output "cluster_arn" {
  value = aws_rds_cluster.main.arn
}

output "master_user_secret_arn" {
  value = aws_rds_cluster.main.master_user_secret[0].secret_arn
}

output "database_name" {
  value = aws_rds_cluster.main.database_name
}

output "security_group_id" {
  value = aws_security_group.aurora.id
}
