###############################################################################
# Outputs — consumed by the GitHub Actions workflow to push SSM params and
# render Kubernetes manifests.
###############################################################################

output "aws_region" {
  value = var.aws_region
}

output "cluster_name" {
  value = module.eks.cluster_name
}

output "cluster_endpoint" {
  value = module.eks.cluster_endpoint
}

output "cluster_certificate_authority_data" {
  value     = module.eks.cluster_certificate_authority_data
  sensitive = true
}

output "cluster_version" {
  value = module.eks.cluster_version
}

output "vpc_id" {
  value = module.vpc.vpc_id
}

output "ecr_repository_urls" {
  value = { for k, v in aws_ecr_repository.images : k => v.repository_url }
}

output "rds_endpoint" {
  value = aws_db_instance.postgres.address
}

output "rds_port" {
  value = aws_db_instance.postgres.port
}

output "cognito_pool_id" {
  value = aws_cognito_user_pool.pool.id
}

output "cognito_client_id" {
  value = aws_cognito_user_pool_client.client.id
}

output "cognito_client_secret" {
  value     = aws_cognito_user_pool_client.client.client_secret
  sensitive = true
}

output "s3_bucket_name" {
  value = aws_s3_bucket.repo_storage.id
}

output "efs_id" {
  value = aws_efs_file_system.draft_storage.id
}

output "efs_access_point_id" {
  value = aws_efs_access_point.main.id
}

output "sns_cache_invalidation_topic_arn" {
  value = aws_sns_topic.cache_invalidation.arn
}

output "repo_cache_invalidation_queue_url" {
  value = aws_sqs_queue.repo_cache_invalidation.url
}

output "workflow_cache_invalidation_queue_url" {
  value = aws_sqs_queue.workflow_cache_invalidation.url
}

output "ses_sender_email" {
  value = aws_sesv2_email_identity.sender.email_identity
}

output "ses_configuration_set_name" {
  value = aws_sesv2_configuration_set.main.configuration_set_name
}

output "frontend_url" {
  value = var.frontend_url
}

output "identity_service_role_arn" {
  value = aws_iam_role.identity_service_role.arn
}

output "repo_service_role_arn" {
  value = aws_iam_role.repo_service_role.arn
}

output "workflow_service_role_arn" {
  value = aws_iam_role.workflow_service_role.arn
}

output "acm_certificate_arn" {
  value       = aws_acm_certificate.primary.arn
  description = "ACM cert covering apex + www. ALB Controller auto-discovers it from Ingress host rules."
}

output "route53_zone_id" {
  value = aws_route53_zone.primary.zone_id
}

output "route53_name_servers" {
  value       = aws_route53_zone.primary.name_servers
  description = "Set these as the authoritative NS for chronovcs.com at your registrar."
}

output "dnssec_ds_record" {
  value       = aws_route53_key_signing_key.primary.ds_record
  description = "DS record — paste into the registrar's DNSSEC panel to complete the chain of trust."
}
