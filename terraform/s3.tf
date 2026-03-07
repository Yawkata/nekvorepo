resource "aws_s3_bucket" "repo_storage" {
  bucket        = "${var.project_name}-repo-blobs"
  force_destroy = true # DEV: Allows 'terraform destroy' even if files exist
}

# Block all public access (Standard Security Practice)
resource "aws_s3_bucket_public_access_block" "repo_storage_block" {
  bucket = aws_s3_bucket.repo_storage.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}