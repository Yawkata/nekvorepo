"""
Shared SQS long-polling consumer for cache-invalidation events.

Used by repo-service and workflow-service to drain their dedicated SQS queues
(fan-out targets of the SNS cache-invalidation topic) so that role-cache
entries are evicted promptly on member removal instead of waiting for the 60s
TTL.

Design notes (2026 best practices):

  - Errors are classified into (a) *fatal* — unrecoverable at runtime, the
    thread exits; and (b) *transient* — exponential backoff with cap.  The
    previous implementation spun in a 5-second tight loop on every error,
    which turned a misconfigured queue or stale credential into log flooding.

  - Fatal errors include: bad credentials (``InvalidClientTokenId``,
    ``SignatureDoesNotMatch``), IAM denial (``AccessDenied``,
    ``AuthorizationError``), and missing queue (``AWS.SimpleQueueService.NonExistentQueue``).
    These will never heal without human intervention, so the thread stops
    after logging once at warning level.  The 60-second role-cache TTL
    remains the documented fallback.

  - Transient errors (network blips, 5xx from SQS, timeout) trigger an
    exponential backoff starting at 1s, capped at 60s, reset after the next
    successful poll.

  - Startup validation: the first ``GetQueueAttributes`` call surfaces
    configuration errors immediately instead of once per poll cycle.

Local dev and production share the exact same code path.  In local dev
(docker-compose) credentials come from env vars and point at LocalStack or a
real AWS account; in EKS they come from IRSA (IAM Roles for Service Accounts).
The boto3 default credential chain handles both without a conditional.
"""
from __future__ import annotations

import json
import time
from typing import Callable

import boto3
import structlog
from botocore.exceptions import ClientError, EndpointConnectionError

log = structlog.get_logger(__name__)

# AWS error codes that will never heal without human intervention.  When any
# of these surface, the consumer thread exits cleanly — the role-cache TTL
# (60 s) remains the documented fallback.
_FATAL_ERROR_CODES = frozenset(
    {
        "InvalidClientTokenId",
        "SignatureDoesNotMatch",
        "AuthFailure",
        "AccessDenied",
        "AccessDeniedException",
        "UnrecognizedClientException",
        "AWS.SimpleQueueService.NonExistentQueue",
        "QueueDoesNotExist",
    }
)

_LONG_POLL_WAIT_SECONDS = 20  # AWS long-poll max — reduces empty-poll cost
_INITIAL_BACKOFF_SECONDS = 1.0
_MAX_BACKOFF_SECONDS = 60.0


def run_cache_invalidation_consumer(
    queue_url: str,
    region_name: str,
    on_invalidate: Callable[[str, str], None],
    service_name: str,
) -> None:
    """
    Long-poll the cache-invalidation SQS queue and invoke ``on_invalidate``
    for each ``{repo_id, user_id}`` message.  Safe to run as a daemon thread.

    No-op when ``queue_url`` is empty — lets local dev disable the background
    consumer without code changes.

    **Idempotency contract**: ``on_invalidate`` MUST be idempotent.  SQS
    guarantees at-least-once delivery, so the same message can be processed
    more than once during a visibility-timeout race, a pod restart between
    handler completion and ``delete_message``, or an SNS fan-out retry.  The
    current caller (role-cache eviction) is naturally idempotent — evicting
    a non-existent cache entry is a no-op — but any future callback added
    here must uphold the same property, or messages will corrupt state.
    """
    if not queue_url:
        log.info("sqs_consumer_disabled", service=service_name)
        return

    try:
        sqs = boto3.client("sqs", region_name=region_name)
    except Exception as exc:  # pragma: no cover — boto3 client init rarely fails
        log.error("sqs_consumer_client_init_failed", service=service_name, error=str(exc))
        return

    # Startup validation — surfaces bad creds / missing queue once at boot
    # instead of once per poll cycle.  Any fatal error here exits the thread.
    try:
        sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in _FATAL_ERROR_CODES:
            log.warning(
                "sqs_consumer_disabled_fatal",
                service=service_name,
                error_code=code,
                queue_url=queue_url,
                hint=(
                    "Unrecoverable AWS error at startup — check credentials, "
                    "IAM policy, and that the queue URL matches terraform output. "
                    "Falling back to the 60-second role-cache TTL."
                ),
            )
            return
        # Non-fatal ClientError: continue to the poll loop; it will retry with backoff.
        log.warning("sqs_consumer_startup_transient", service=service_name, error_code=code)
    except EndpointConnectionError as exc:
        log.warning("sqs_consumer_startup_unreachable", service=service_name, error=str(exc))
    except Exception as exc:
        log.warning("sqs_consumer_startup_unexpected", service=service_name, error=str(exc))

    log.info("sqs_consumer_started", service=service_name, queue_url=queue_url)

    backoff = _INITIAL_BACKOFF_SECONDS
    while True:
        try:
            resp = sqs.receive_message(
                QueueUrl=queue_url,
                MaxNumberOfMessages=10,
                WaitTimeSeconds=_LONG_POLL_WAIT_SECONDS,
            )
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in _FATAL_ERROR_CODES:
                log.warning(
                    "sqs_consumer_stopping_fatal",
                    service=service_name,
                    error_code=code,
                    queue_url=queue_url,
                )
                return
            log.error(
                "sqs_consumer_transient_error",
                service=service_name,
                error_code=code,
                backoff_seconds=backoff,
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
            continue
        except EndpointConnectionError as exc:
            log.error(
                "sqs_consumer_unreachable",
                service=service_name,
                error=str(exc),
                backoff_seconds=backoff,
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
            continue
        except Exception as exc:
            log.error(
                "sqs_consumer_unexpected_error",
                service=service_name,
                error=str(exc),
                backoff_seconds=backoff,
            )
            time.sleep(backoff)
            backoff = min(backoff * 2, _MAX_BACKOFF_SECONDS)
            continue

        # Successful poll — reset backoff.
        backoff = _INITIAL_BACKOFF_SECONDS

        for msg in resp.get("Messages", []):
            try:
                body = json.loads(msg["Body"])
                on_invalidate(body["repo_id"], body["user_id"])
                sqs.delete_message(QueueUrl=queue_url, ReceiptHandle=msg["ReceiptHandle"])
            except Exception as exc:
                # Per-message failures are logged but do not stop the consumer.
                # The message will re-appear after its visibility timeout and
                # land in the DLQ after maxReceiveCount attempts.
                log.error(
                    "sqs_message_processing_failed",
                    service=service_name,
                    error=str(exc),
                )
