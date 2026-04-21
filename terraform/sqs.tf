###############################################################################
# SQS — Role-cache invalidation queue
#
# When a member is removed or demoted, identity-service publishes a message
# here.  repo-service and workflow-service consume it to immediately evict
# the affected user's cached role entry instead of waiting for the 60-second
# TTL (Well-Architected: cache coherence, least-privilege per-service queues).
###############################################################################

# ---------------------------------------------------------------------------
# Dead-Letter Queue — receives messages that fail processing 3 times.
# Retention: 7 days (enough time for on-call to investigate).
# ---------------------------------------------------------------------------

resource "aws_sqs_queue" "cache_invalidation_dlq" {
  name = "${var.project_name}-cache-invalidation-dlq"

  message_retention_seconds = 604800 # 7 days

  tags = {
    Project     = var.project_name
    Purpose     = "cache-invalidation-dlq"
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

# ---------------------------------------------------------------------------
# Main queue
#
# Standard (not FIFO) — order is irrelevant for cache invalidation; a second
# delivery of the same message is idempotent (cache evict is always safe).
#
# Visibility timeout = 30 s: the consumer (one short HTTP call) comfortably
# fits within this window.  Message retention = 1 day: stale invalidation
# events beyond 24 h are useless (role cache TTL is 60 s).
# ---------------------------------------------------------------------------

resource "aws_sqs_queue" "cache_invalidation" {
  name = "${var.project_name}-cache-invalidation"

  visibility_timeout_seconds = 30
  message_retention_seconds  = 86400 # 1 day

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.cache_invalidation_dlq.arn
    maxReceiveCount     = 3
  })

  # Encrypt at rest — no extra cost, security best practice.
  sqs_managed_sse_enabled = true

  tags = {
    Project     = var.project_name
    Purpose     = "role-cache-invalidation"
    Environment = "dev"
    ManagedBy   = "terraform"
  }
}

# ---------------------------------------------------------------------------
# Queue policy — explicitly allow only the three service principals to act
# on this queue (deny-by-default; Well-Architected Security pillar).
# ---------------------------------------------------------------------------

resource "aws_sqs_queue_policy" "cache_invalidation" {
  queue_url = aws_sqs_queue.cache_invalidation.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowIdentityServicePublish"
        Effect = "Allow"
        Principal = {
          AWS = aws_iam_role.identity_service_role.arn
        }
        Action   = ["sqs:SendMessage"]
        Resource = aws_sqs_queue.cache_invalidation.arn
      },
      {
        Sid    = "AllowConsumerServicesConsume"
        Effect = "Allow"
        Principal = {
          AWS = [
            aws_iam_role.repo_service_role.arn,
            aws_iam_role.workflow_service_role.arn,
          ]
        }
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
