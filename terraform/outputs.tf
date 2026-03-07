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