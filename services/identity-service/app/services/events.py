"""
SNS event publisher for role-cache invalidation.

When a member is removed or demoted, identity-service publishes a single
message to the SNS cache-invalidation topic.  SNS fans the message out to
every subscribed SQS queue (one per consuming service), so each service
receives an independent copy and evicts the affected user's cached role entry.

This replaces the previous single-SQS-queue approach where two services
competed for the same message — meaning only one ever received it.

Graceful no-op when SNS_CACHE_INVALIDATION_TOPIC_ARN is not set (local dev /
CI without AWS credentials).  Errors are logged and swallowed so a transient
SNS outage never blocks a member removal; the 60-second role cache TTL is the
fallback.
"""
import json
import os

import boto3
import structlog
from botocore.exceptions import BotoCoreError, ClientError

log = structlog.get_logger(__name__)

_sns_client = boto3.client(
    "sns",
    region_name=os.getenv("AWS_REGION", "us-east-1"),
)


def publish_cache_invalidation(repo_id: str, user_id: str, topic_arn: str = "") -> None:
    """
    Publish a cache-invalidation event to the SNS fan-out topic.

    No-op when topic_arn is empty.  SNS delivers an independent copy to every
    subscribed SQS queue, guaranteeing all consumer services receive the event.
    """
    if not topic_arn:
        return
    try:
        _sns_client.publish(
            TopicArn=topic_arn,
            Message=json.dumps({"repo_id": repo_id, "user_id": user_id}),
        )
        log.info("cache_invalidation_published", repo_id=repo_id, user_id=user_id)
    except (BotoCoreError, ClientError) as exc:
        log.error(
            "cache_invalidation_publish_failed",
            repo_id=repo_id,
            user_id=user_id,
            error=str(exc),
        )
