"""
SQS publisher for cache invalidation messages.

Published after member removal so all pods in workflow-service and repo-service
immediately evict the removed user's cached role entry instead of waiting for
the 60-second TTL.

Graceful no-op if SQS_CACHE_INVALIDATION_QUEUE_URL is not set (local dev / CI).
"""
import json
import logging
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

log = logging.getLogger(__name__)

_sqs_client = boto3.client(
    "sqs",
    region_name=os.getenv("AWS_REGION", "us-east-1"),
)


def publish_cache_invalidation(repo_id: str, user_id: str, queue_url: str = "") -> None:
    """
    Publish a cache invalidation message to SQS.

    No-op if queue_url is empty.  Errors are logged and swallowed so that
    a transient SQS outage never blocks a member removal — worst case the
    role cache expires naturally after 60 seconds.
    """
    if not queue_url:
        return
    try:
        _sqs_client.send_message(
            QueueUrl=queue_url,
            MessageBody=json.dumps({"repo_id": repo_id, "user_id": user_id}),
        )
        log.info("cache_invalidation_published repo_id=%s user_id=%s", repo_id, user_id)
    except (BotoCoreError, ClientError) as exc:
        log.error("cache_invalidation_publish_failed repo_id=%s user_id=%s error=%s", repo_id, user_id, exc)
