"""
SES email notifications for invite and membership lifecycle events.

Usage:
    from app.services.notifications import send_invite_notification, ...

All errors are logged and swallowed — notification failures never block or
roll back database state.  If SES_FROM_EMAIL is empty, all functions are no-ops.
"""
import logging
import os

import boto3
from botocore.exceptions import BotoCoreError, ClientError

log = logging.getLogger(__name__)

_ses_client = boto3.client(
    "ses",
    region_name=os.getenv("AWS_REGION", "us-east-1"),
)


def _send(
    from_email: str,
    recipient_email: str | None,
    subject: str,
    body_text: str,
    body_html: str,
) -> None:
    if not from_email or not recipient_email:
        return
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
        log.info("notification_sent subject=%r recipient=%s", subject, recipient_email)
    except (BotoCoreError, ClientError) as exc:
        log.error("notification_failed subject=%r recipient=%s error=%s", subject, recipient_email, exc)


def send_invite_notification(
    recipient_email: str | None,
    repo_name: str,
    role: str,
    accept_url: str,
    from_email: str = "",
) -> None:
    """Send an invite email with an accept link."""
    _send(
        from_email=from_email,
        recipient_email=recipient_email,
        subject=f"[Chrono VCS] You've been invited to {repo_name}",
        body_text=(
            f"You have been invited to join the repository '{repo_name}' as a {role}.\n\n"
            f"Accept your invitation here:\n{accept_url}\n\n"
            "This link expires in 72 hours."
        ),
        body_html=(
            f"<p>You have been invited to join the repository <strong>{repo_name}</strong> "
            f"as a <strong>{role}</strong>.</p>"
            f"<p><a href=\"{accept_url}\">Accept invitation</a></p>"
            "<p>This link expires in 72 hours.</p>"
        ),
    )


def send_role_changed_notification(
    recipient_email: str | None,
    repo_name: str,
    old_role: str,
    new_role: str,
    from_email: str = "",
) -> None:
    """Notify a member that their role has been changed."""
    _send(
        from_email=from_email,
        recipient_email=recipient_email,
        subject=f"[Chrono VCS] Your role in {repo_name} has changed",
        body_text=(
            f"Your role in the repository '{repo_name}' has been changed "
            f"from {old_role} to {new_role} by an administrator."
        ),
        body_html=(
            f"<p>Your role in the repository <strong>{repo_name}</strong> has been changed "
            f"from <strong>{old_role}</strong> to <strong>{new_role}</strong> "
            "by an administrator.</p>"
        ),
    )


def send_removed_notification(
    recipient_email: str | None,
    repo_name: str,
    from_email: str = "",
) -> None:
    """Notify a member that they have been removed from a repository."""
    _send(
        from_email=from_email,
        recipient_email=recipient_email,
        subject=f"[Chrono VCS] You have been removed from {repo_name}",
        body_text=(
            f"You have been removed from the repository '{repo_name}' by an administrator.\n\n"
            "Your pending commits have been cancelled."
        ),
        body_html=(
            f"<p>You have been removed from the repository <strong>{repo_name}</strong> "
            "by an administrator.</p>"
            "<p>Your pending commits have been cancelled.</p>"
        ),
    )
