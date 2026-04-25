import logging

from django.conf import settings
from django.core.mail import send_mail
logger = logging.getLogger(__name__)

def send_email(
    subject: str,
    message: str,
    recipient_list: list,
    from_email: str | None = None,
    fail_silently: bool = False,
) -> int:
    """Task to send emails asynchronously."""
    if from_email is None:
        from_email = settings.DEFAULT_FROM_EMAIL
    mail = send_mail(
        subject,
        message,
        from_email,
        recipient_list,
        fail_silently=fail_silently,
    )

    logger.info("email sent: to:      <%s>", str(recipient_list))
    logger.info("email sent: from:    <%s>", str(from_email))
    logger.info("email sent: subject:  %s", str(subject))
    logger.info("email sent: message:  %s", str(message))

    return mail
