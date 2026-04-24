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

output "sns_cache_invalidation_topic_arn" {
  description = "Set as SNS_CACHE_INVALIDATION_TOPIC_ARN in identity-service. The topic fans out to both consumer queues."
  value       = aws_sns_topic.cache_invalidation.arn
}

output "repo_cache_invalidation_queue_url" {
  description = "Set as SQS_CACHE_INVALIDATION_QUEUE_URL in repo-service."
  value       = aws_sqs_queue.repo_cache_invalidation.url
}

output "workflow_cache_invalidation_queue_url" {
  description = "Set as SQS_CACHE_INVALIDATION_QUEUE_URL in workflow-service."
  value       = aws_sqs_queue.workflow_cache_invalidation.url
}

output "ses_sender_email" {
  description = "Verified SES sender address. Set as SES_FROM_EMAIL in identity-service."
  value       = aws_sesv2_email_identity.sender.email_identity
}

output "ses_configuration_set_name" {
  description = "SES configuration set name. Set as SES_CONFIGURATION_SET_NAME in identity-service."
  value       = aws_sesv2_configuration_set.main.configuration_set_name
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