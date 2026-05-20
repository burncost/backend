import logging
from typing import Optional

logger = logging.getLogger(__name__)


def send_email(
    to_email: str,
    subject: str,
    html_content: str,
    from_email: Optional[str] = None,
    from_name: Optional[str] = None
):
    """Send email"""
    logger.info(f"Sending email to {to_email}: {subject}")
    # TODO: Implement actual email sending
    # Would use SMTP, SendGrid, Mailgun, etc.
    pass