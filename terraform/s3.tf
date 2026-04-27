resource "aws_s3_bucket" "repo_storage" {
  bucket        = "${var.project_name}-repo-blobs"
  force_destroy = true
}

resource "aws_s3_bucket_logging" "repo_storage" {
  bucket        = aws_s3_bucket.repo_storage.id
  target_bucket = aws_s3_bucket.access_logs.id
  target_prefix = "repo-blobs/"
}

resource "aws_s3_bucket" "access_logs" {
  bucket        = "${var.project_name}-s3-access-logs"
  force_destroy = true
}

resource "aws_s3_bucket_public_access_block" "access_logs" {
  bucket                  = aws_s3_bucket.access_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "access_logs" {
  bucket = aws_s3_bucket.access_logs.id
  rule {
    id     = "expire-old-logs"
    status = "Enabled"
    filter {}
    expiration {
      days = 90
    }
  }
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

    # Move infrequently-accessed blobs to cheaper storage after 30 days.
    transition {
      days          = 30
      storage_class = "INTELLIGENT_TIERING"
    }

    # IMPORTANT: Do NOT expire current versions.  Repo blobs are immutable
    # commit artefacts — deleting them would corrupt commit history.
    # Instead, expire only noncurrent versions (overwritten/deleted objects)
    # and clean up orphaned delete markers to keep the bucket tidy.
    noncurrent_version_expiration {
      noncurrent_days = 90
    }

    expiration {
      expired_object_delete_marker = true
    }
  }
}

resource "aws_s3_bucket_ownership_controls" "repo_storage" {
  bucket = aws_s3_bucket.repo_storage.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}