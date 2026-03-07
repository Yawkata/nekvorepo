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

resource "aws_s3_bucket_server_side_encryption_configuration" "repo_storage" {
  bucket = aws_s3_bucket.repo_storage.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_versioning" "repo_versioning" {
  bucket = aws_s3_bucket.repo_storage.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "repo_storage" {
  bucket = aws_s3_bucket.repo_storage.id

  rule {
    id     = "archive-old-blobs"
    status = "Enabled"

    filter {}

    transition {
      days          = 30
      storage_class = "INTELLIGENT_TIERING"
    }

    expiration {
      days = 90
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "repo_storage" {
  bucket = aws_s3_bucket.repo_storage.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}