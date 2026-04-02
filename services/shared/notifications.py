"""
SES email notifications for commit lifecycle events.

Usage:
    from shared.notifications import send_notification

    send_notification(
        event="approved",
        recipient_email="author@example.com",
        repo_name="my-repo",
        commit_hash="abc123",
        from_email=settings.SES_FROM_EMAIL,
    )

All errors are logged and swallowed — notification failures never block or
roll back database state.  If SES_FROM_EMAIL is empty, the function is a no-op.
"""
import logging
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

log = logging.getLogger(__name__)

# Module-level singleton — credential resolution + connection pool init is
# expensive.  Re-using across calls is the standard boto3 pattern.
_ses_client = boto3.client(
    "ses",
    region_name=os.getenv("AWS_REGION", "us-east-1"),
)

# ---------------------------------------------------------------------------
# Email templates
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, dict] = {
    "approved": {
        "subject": "[Chrono VCS] Your commit was approved",
        "text": (
            "Your commit has been approved and merged.\n\n"
            "Repository : {repo_name}\n"
            "Commit     : {commit_hash}\n\n"
            "You can now view the latest version in Chrono VCS."
        ),
        "html": (
            "<p>Your commit has been <strong>approved</strong> and merged.</p>"
            "<ul>"
            "<li><b>Repository:</b> {repo_name}</li>"
            "<li><b>Commit:</b> <code>{commit_hash}</code></li>"
            "</ul>"
            "<p>You can now view the latest version in Chrono VCS.</p>"
        ),
    },
    "reviewer_rejected": {
        "subject": "[Chrono VCS] Your commit was rejected",
        "text": (
            "Your commit was rejected by a reviewer.\n\n"
            "Repository : {repo_name}\n"
            "Commit     : {commit_hash}\n"
            "{reviewer_comment_line}\n"
            "You can open a new draft to revise and resubmit."
        ),
        "html": (
            "<p>Your commit was <strong>rejected</strong> by a reviewer.</p>"
            "<ul>"
            "<li><b>Repository:</b> {repo_name}</li>"
            "<li><b>Commit:</b> <code>{commit_hash}</code></li>"
            "{reviewer_comment_html}"
            "</ul>"
            "<p>You can open a new draft to revise and resubmit.</p>"
        ),
    },
    "sibling_rejected": {
        "subject": "[Chrono VCS] Your draft was superseded",
        "text": (
            "Another commit was approved for this repository before yours, "
            "so your pending draft has been marked as superseded.\n\n"
            "Repository : {repo_name}\n\n"
            "Your draft has been preserved.  Open it to rebase and resubmit."
        ),
        "html": (
            "<p>Another commit was approved for this repository before yours, "
            "so your pending draft has been marked as <strong>superseded</strong>.</p>"
            "<ul>"
            "<li><b>Repository:</b> {repo_name}</li>"
            "</ul>"
            "<p>Your draft has been preserved. Open it to rebase and resubmit.</p>"
        ),
    },
}


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------

def send_notification(
    event: str,
    recipient_email: str | None,
    repo_name: str,
    commit_hash: str = "",
    reviewer_comment: str | None = None,
    from_email: str = "",
) -> None:
    """
    Send a lifecycle notification email via SES.

    Parameters
    ----------
    event:             One of "approved", "reviewer_rejected", "sibling_rejected".
    recipient_email:   Destination address.  No-op if None or empty.
    repo_name:         Human-readable repository name shown in the email.
    commit_hash:       Commit hash (used in approved / reviewer_rejected).
    reviewer_comment:  Optional reviewer note (reviewer_rejected only).
    from_email:        SES-verified sender address.  No-op if empty.
    """
    if not from_email or not recipient_email:
        return

    template = _TEMPLATES.get(event)
    if template is None:
        log.warning("send_notification: unknown event %r — skipped", event)
        return

    # Build reviewer comment lines for templates that use them
    reviewer_comment_line = (
        f"Reviewer comment: {reviewer_comment}\n" if reviewer_comment else ""
    )
    reviewer_comment_html = (
        f"<li><b>Reviewer comment:</b> {reviewer_comment}</li>"
        if reviewer_comment
        else ""
    )

    subject = template["subject"]
    body_text = template["text"].format(
        repo_name=repo_name,
        commit_hash=commit_hash,
        reviewer_comment_line=reviewer_comment_line,
    )
    body_html = template["html"].format(
        repo_name=repo_name,
        commit_hash=commit_hash,
        reviewer_comment_html=reviewer_comment_html,
    )

    try:
        _ses_client.send_email(
            Source=from_email,
            Destination={"ToAddresses": [recipient_email]},
            Message={
                "Subject": {"Data": subject, "Charset": "UTF-8"},
                "Body": {
                    "Text": {"Data": body_text, "Charset": "UTF-8"},
                    "Html": {"Data": body_html, "Charset": "UTF-8"},
                },
            },
        )
        log.info(
            "notification_sent event=%s recipient=%s repo=%s",
            event,
            recipient_email,
            repo_name,
        )
    except (BotoCoreError, ClientError) as exc:
        # Notification failures are non-fatal — log and continue.
        log.error(
            "notification_failed event=%s recipient=%s error=%s",
            event,
            recipient_email,
            exc,
        )
