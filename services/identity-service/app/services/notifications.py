"""
SES email notifications for invite and membership lifecycle events.

Usage:
    from app.services.notifications import send_invite_notification, ...

All errors are logged and swallowed — notification failures never block or
roll back database state.  If SES_FROM_EMAIL is empty, all functions are no-ops.
"""
import html
import os

import boto3
import structlog
from botocore.exceptions import BotoCoreError, ClientError

log = structlog.get_logger(__name__)

_ses_client = boto3.client(
    "ses",
    region_name=os.getenv("AWS_REGION", "us-east-1"),
)

# Optional: attach a SES configuration set to every outbound message so that
# bounce/complaint suppression and delivery tracking are active.
# Set SES_CONFIGURATION_SET_NAME in the environment to enable (matches the
# name provisioned in ses.tf: "<project_name>-email-config").
# Leave blank to send without a configuration set (safe for local dev).
_CONFIGURATION_SET = os.getenv("SES_CONFIGURATION_SET_NAME", "")


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
        kwargs: dict = dict(
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
        if _CONFIGURATION_SET:
            kwargs["ConfigurationSetName"] = _CONFIGURATION_SET
        _ses_client.send_email(**kwargs)
        log.info("notification_sent", subject=subject, recipient=recipient_email)
    except (BotoCoreError, ClientError) as exc:
        log.error("notification_failed", subject=subject, recipient=recipient_email, error=str(exc))


def send_invite_notification(
    recipient_email: str | None,
    repo_name: str,
    role: str,
    accept_url: str,
    from_email: str = "",
) -> None:
    """Send an invite email with an accept link."""
    safe_repo = html.escape(repo_name)
    safe_role = html.escape(role)
    safe_url = html.escape(accept_url)
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
            f"<p>You have been invited to join the repository <strong>{safe_repo}</strong> "
            f"as a <strong>{safe_role}</strong>.</p>"
            f'<p><a href="{safe_url}">Accept invitation</a></p>'
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
    safe_repo = html.escape(repo_name)
    safe_old = html.escape(old_role)
    safe_new = html.escape(new_role)
    _send(
        from_email=from_email,
        recipient_email=recipient_email,
        subject=f"[Chrono VCS] Your role in {repo_name} has changed",
        body_text=(
            f"Your role in the repository '{repo_name}' has been changed "
            f"from {old_role} to {new_role} by an administrator."
        ),
        body_html=(
            f"<p>Your role in the repository <strong>{safe_repo}</strong> has been changed "
            f"from <strong>{safe_old}</strong> to <strong>{safe_new}</strong> "
            "by an administrator.</p>"
        ),
    )


def send_removed_notification(
    recipient_email: str | None,
    repo_name: str,
    from_email: str = "",
) -> None:
    """Notify a member that they have been removed from a repository."""
    safe_repo = html.escape(repo_name)
    _send(
        from_email=from_email,
        recipient_email=recipient_email,
        subject=f"[Chrono VCS] You have been removed from {repo_name}",
        body_text=(
            f"You have been removed from the repository '{repo_name}' by an administrator.\n\n"
            "Your pending commits have been cancelled."
        ),
        body_html=(
            f"<p>You have been removed from the repository <strong>{safe_repo}</strong> "
            "by an administrator.</p>"
            "<p>Your pending commits have been cancelled.</p>"
        ),
    )
