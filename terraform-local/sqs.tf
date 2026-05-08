###############################################################################
# Messaging — Role-cache invalidation (SNS fan-out → per-service SQS queues)
#
# Problem with a single shared SQS queue:
#   SQS delivers each message to EXACTLY ONE consumer.  If repo-service and
#   workflow-service both poll the same queue they compete for messages — the
#   winning service evicts its cache, the losing service never sees the event
#   and serves stale role data until the 60-second TTL expires naturally.
#
# Solution — SNS fan-out (publish/subscribe):
#   identity-service  ──► SNS topic
#                              ├──► SQS queue (repo-service)
#                              └──► SQS queue (workflow-service)
#
#   SNS delivers an independent copy of every message to each subscribed queue.
#   Each service consumes from its own isolated queue, with its own visibility
#   window, retry counter, and DLQ.  Adding a future consumer requires no
#   changes to identity-service — just a new SNS subscription.
###############################################################################

# ---------------------------------------------------------------------------
# SNS Topic — the fan-out hub
# ---------------------------------------------------------------------------

resource "aws_sns_topic" "cache_invalidation" {
  name = "${var.project_name}-cache-invalidation"

  # Encrypt in transit and at rest (managed key, no extra cost).
  kms_master_key_id = "alias/aws/sns"

  tags = {
    Project     = var.project_name
    Purpose     = "role-cache-invalidation-fanout"
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

# Allow only identity-service to publish to the topic.
resource "aws_sns_topic_policy" "cache_invalidation" {
  arn = aws_sns_topic.cache_invalidation.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowIdentityServicePublish"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.identity_service_role.arn
        }
        Action   = "SNS:Publish"
        Resource = aws_sns_topic.cache_invalidation.arn
      },
    ]
  })
}

# ---------------------------------------------------------------------------
# repo-service — dedicated SQS queue + DLQ
# ---------------------------------------------------------------------------

resource "aws_sqs_queue" "repo_cache_invalidation_dlq" {
  name                      = "${var.project_name}-repo-cache-invalidation-dlq"
  message_retention_seconds = 604800 # 7 days
  sqs_managed_sse_enabled   = true

  tags = {
    Project     = var.project_name
    Purpose     = "repo-cache-invalidation-dlq"
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

resource "aws_sqs_queue" "repo_cache_invalidation" {
  name = "${var.project_name}-repo-cache-invalidation"

  visibility_timeout_seconds = 30
  message_retention_seconds  = 86400 # 1 day
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.repo_cache_invalidation_dlq.arn
    maxReceiveCount     = 3
  })

  tags = {
    Project     = var.project_name
    Purpose     = "repo-cache-invalidation"
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

# Allow SNS to deliver messages to repo-service's queue.
resource "aws_sqs_queue_policy" "repo_cache_invalidation" {
  queue_url = aws_sqs_queue.repo_cache_invalidation.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowSNSDelivery"
        Effect = "Allow"
        Principal = {
          Service = "sns.amazonaws.com"
        }
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.repo_cache_invalidation.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = aws_sns_topic.cache_invalidation.arn
          }
        }
      },
    ]
  })
}

# Subscribe repo-service's queue to the SNS topic.
# raw_message_delivery = true — SQS receives the raw JSON body that identity-service
# published, without an SNS envelope wrapper.  The consuming service sees exactly:
#   {"repo_id": "...", "user_id": "..."}
# instead of a nested SNS notification object it would have to unwrap.
resource "aws_sns_topic_subscription" "repo_cache_invalidation" {
  topic_arn            = aws_sns_topic.cache_invalidation.arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.repo_cache_invalidation.arn
  raw_message_delivery = true
}

# ---------------------------------------------------------------------------
# workflow-service — dedicated SQS queue + DLQ
# ---------------------------------------------------------------------------

resource "aws_sqs_queue" "workflow_cache_invalidation_dlq" {
  name                      = "${var.project_name}-workflow-cache-invalidation-dlq"
  message_retention_seconds = 604800 # 7 days
  sqs_managed_sse_enabled   = true

  tags = {
    Project     = var.project_name
    Purpose     = "workflow-cache-invalidation-dlq"
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

resource "aws_sqs_queue" "workflow_cache_invalidation" {
  name = "${var.project_name}-workflow-cache-invalidation"

  visibility_timeout_seconds = 30
  message_retention_seconds  = 86400 # 1 day
  sqs_managed_sse_enabled    = true

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.workflow_cache_invalidation_dlq.arn
    maxReceiveCount     = 3
  })

  tags = {
    Project     = var.project_name
    Purpose     = "workflow-cache-invalidation"
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

resource "aws_sqs_queue_policy" "workflow_cache_invalidation" {
  queue_url = aws_sqs_queue.workflow_cache_invalidation.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowSNSDelivery"
        Effect = "Allow"
        Principal = {
          Service = "sns.amazonaws.com"
        }
        Action   = "sqs:SendMessage"
        Resource = aws_sqs_queue.workflow_cache_invalidation.arn
        Condition = {
          ArnEquals = {
            "aws:SourceArn" = aws_sns_topic.cache_invalidation.arn
          }
        }
      },
    ]
  })
}

resource "aws_sns_topic_subscription" "workflow_cache_invalidation" {
  topic_arn            = aws_sns_topic.cache_invalidation.arn
  protocol             = "sqs"
  endpoint             = aws_sqs_queue.workflow_cache_invalidation.arn
  raw_message_delivery = true
}
