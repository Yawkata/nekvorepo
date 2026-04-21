output "rds_endpoint" {
  value = aws_db_instance.postgres.address
}

# output "bastion_public_ip" {
#   value = aws_instance.bastion.public_ip
# }

output "cognito_pool_id" {
  value = aws_cognito_user_pool.pool.id
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.client.id
}

output "s3_bucket_name" {
  value = aws_s3_bucket.repo_storage.id
}

output "dev_access_key_id" {
  value = aws_iam_access_key.dev_key.id
}

output "dev_secret_access_key" {
  value     = aws_iam_access_key.dev_key.secret
  sensitive = true
}

output "cognito_client_secret" {
  value     = aws_cognito_user_pool_client.client.client_secret
  sensitive = true
}

output "sqs_cache_invalidation_queue_url" {
  description = "Set as SQS_CACHE_INVALIDATION_QUEUE_URL in identity-service, repo-service, and workflow-service."
  value       = aws_sqs_queue.cache_invalidation.url
}

output "ses_sender_email" {
  description = "Verified SES sender address. Set as SES_FROM_EMAIL in identity-service."
  value       = aws_sesv2_email_identity.sender.email_identity
}

output "frontend_url" {
  description = "Frontend base URL used to build invite accept links. Set as INVITE_ACCEPT_BASE_URL in identity-service."
  value       = var.frontend_url
}

output "identity_service_role_arn" {
  description = "IAM role ARN for identity-service. Attach to the compute resource (EC2 instance profile / EKS service account)."
  value       = aws_iam_role.identity_service_role.arn
}

output "repo_service_role_arn" {
  description = "IAM role ARN for repo-service."
  value       = aws_iam_role.repo_service_role.arn
}

output "workflow_service_role_arn" {
  description = "IAM role ARN for workflow-service."
  value       = aws_iam_role.workflow_service_role.arn
}