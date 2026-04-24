###############################################################################
# IAM — per-service roles (least-privilege, Well-Architected Security pillar)
#
# Each microservice gets its own IAM role so that a compromise of one service
# cannot be used to escalate into another. Trust principal is pods.eks.amazonaws.com
# (EKS Pod Identity) — mapped to a k8s ServiceAccount via
# aws_eks_pod_identity_association resources in pod_identity.tf.
###############################################################################

# ---------------------------------------------------------------------------
# identity-service
# Needs: Cognito admin operations + SES SendEmail + SQS publish
# ---------------------------------------------------------------------------

resource "aws_iam_role" "identity_service_role" {
  name = "${var.project_name}-identity-service-role"

  # EKS Pod Identity trust. sts:TagSession is REQUIRED — the pod-identity
  # agent tags the assumed session with cluster/namespace/SA so CloudTrail
  # attributes every AWS call to a specific pod. Without TagSession, the
  # agent's AssumeRole call is silently rejected.
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Action    = ["sts:AssumeRole", "sts:TagSession"]
        Principal = { Service = "pods.eks.amazonaws.com" }
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

resource "aws_iam_role_policy" "identity_sns_publish" {
  name = "sns-cache-invalidation-publish"
  role = aws_iam_role.identity_service_role.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "PublishCacheInvalidationTopic"
        Effect   = "Allow"
        Action   = ["sns:Publish"]
        Resource = aws_sns_topic.cache_invalidation.arn
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
        Effect    = "Allow"
        Action    = ["sts:AssumeRole", "sts:TagSession"]
        Principal = { Service = "pods.eks.amazonaws.com" }
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
        Resource = aws_sqs_queue.repo_cache_invalidation.arn
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
        Effect    = "Allow"
        Action    = ["sts:AssumeRole", "sts:TagSession"]
        Principal = { Service = "pods.eks.amazonaws.com" }
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
        Resource = aws_sqs_queue.workflow_cache_invalidation.arn
      },
    ]
  })
}