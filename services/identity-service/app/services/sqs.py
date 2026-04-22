"""
SUPERSEDED — use app.services.events instead.

This module published cache-invalidation messages directly to a single shared
SQS queue.  That design had a critical flaw: SQS delivers each message to
exactly one consumer, so only one of (repo-service, workflow-service) would
receive any given invalidation event.

The replacement (events.py) publishes to an SNS topic which fans out to a
dedicated SQS queue per service, guaranteeing every consumer receives a copy.
"""

raise ImportError(
    "app.services.sqs is superseded. "
    "Import from app.services.events instead."
)
