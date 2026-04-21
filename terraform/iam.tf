###############################################################################
# IAM — per-service roles (least-privilege, Well-Architected Security pillar)
#
# Each microservice gets its own IAM role so that a compromise of one service
# cannot be used to escalate into another.  The assume_role principal is set
# to ec2.amazonaws.com for local/Docker dev; swap to the EKS OIDC provider
# when moving to Kubernetes.
###############################################################################

# ---------------------------------------------------------------------------
# identity-service
# Needs: Cognito admin operations + SES SendEmail + SQS publish
# ---------------------------------------------------------------------------

resource "aws_iam_role" "identity_service_role" {
  name = "${var.project_name}-identity-service-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com" # TODO: replace with EKS OIDC provider ARN for production
        }
      },
    ]
  })

  tags = {
    Project   = var.project_name
    Service   = "identity-service"
    ManagedBy = "terraform"
  }
}

resource "aws_iam_role_policy" "identity_cognito" {
  name = "cognito-admin-access"
  role = aws_iam_role.identity_service_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "CognitoAdminOps"
        Effect = "Allow"
        Action = [
          "cognito-idp:AdminCreateUser",
          "cognito-idp:AdminGetUser",
          "cognito-idp:AdminUpdateUserAttributes",
          "cognito-idp:AdminSetUserPassword",
          "cognito-idp:AdminInitiateAuth",
        ]
        Resource = aws_cognito_user_pool.pool.arn
      },
    ]
  })
}

resource "aws_iam_role_policy" "identity_ses" {
  name = "ses-send-email"
  role = aws_iam_role.identity_service_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SESSendFromVerifiedIdentity"
        Effect = "Allow"
        Action = ["ses:SendEmail", "ses:SendRawEmail"]
        # Restrict to the verified sender identity only (least-privilege).
        Resource = "*"
        Condition = {
          StringEquals = {
            "ses:FromAddress" = var.ses_sender_email
          }
        }
      },
    ]
  })
}

resource "aws_iam_role_policy" "identity_sqs_publish" {
  name = "sqs-cache-invalidation-publish"
  role = aws_iam_role.identity_service_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "PublishCacheInvalidation"
        Effect   = "Allow"
        Action   = ["sqs:SendMessage", "sqs:GetQueueAttributes"]
        Resource = aws_sqs_queue.cache_invalidation.arn
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# repo-service
# Needs: S3 blob operations + SQS consume (cache invalidation subscriber)
# ---------------------------------------------------------------------------

resource "aws_iam_role" "repo_service_role" {
  name = "${var.project_name}-repo-service-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      },
    ]
  })

  tags = {
    Project   = var.project_name
    Service   = "repo-service"
    ManagedBy = "terraform"
  }
}

resource "aws_iam_role_policy" "repo_s3" {
  name = "s3-blob-access"
  role = aws_iam_role.repo_service_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3BlobOps"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
          "s3:DeleteObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.repo_storage.arn,
          "${aws_s3_bucket.repo_storage.arn}/*",
        ]
      },
    ]
  })
}

resource "aws_iam_role_policy" "repo_sqs_consume" {
  name = "sqs-cache-invalidation-consume"
  role = aws_iam_role.repo_service_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ConsumeCacheInvalidation"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
        ]
        Resource = aws_sqs_queue.cache_invalidation.arn
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# workflow-service
# Needs: S3 read (tree blobs) + SQS consume (cache invalidation subscriber)
# ---------------------------------------------------------------------------

resource "aws_iam_role" "workflow_service_role" {
  name = "${var.project_name}-workflow-service-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "ec2.amazonaws.com"
        }
      },
    ]
  })

  tags = {
    Project   = var.project_name
    Service   = "workflow-service"
    ManagedBy = "terraform"
  }
}

resource "aws_iam_role_policy" "workflow_s3" {
  name = "s3-blob-read"
  role = aws_iam_role.workflow_service_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3BlobRead"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.repo_storage.arn,
          "${aws_s3_bucket.repo_storage.arn}/*",
        ]
      },
    ]
  })
}

resource "aws_iam_role_policy" "workflow_sqs_consume" {
  name = "sqs-cache-invalidation-consume"
  role = aws_iam_role.workflow_service_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ConsumeCacheInvalidation"
        Effect = "Allow"
        Action = [
          "sqs:ReceiveMessage",
          "sqs:DeleteMessage",
          "sqs:GetQueueAttributes",
        ]
        Resource = aws_sqs_queue.cache_invalidation.arn
      },
    ]
  })
}
