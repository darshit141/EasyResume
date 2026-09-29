"""Real email delivery for OTP codes, via Django's own mail system.

Uses ``send_mail`` (not a raw ``print``) so the exact same call path works
in every environment: under the console backend (local dev default) it
prints the full email to the server console; once ``EMAIL_HOST`` is set in
production, the identical call sends a real email — no code change needed
to go from dev to production, only configuration.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def send_otp_email(to_email, otp, purpose="login", expiry_minutes=5):
    """Send a one-time code to ``to_email``. ``purpose`` is either "login"
    or "password reset" and only changes the copy, never the code path.
    ``expiry_minutes`` is the admin-configured lifetime, so the email never
    promises a different window from the one the server enforces."""

    subject = (
        "Your IntelliResume login code"
        if purpose == "login"
        else "Your IntelliResume password reset code"
    )
    action_text = (
        "Use this code to finish signing in:"
        if purpose == "login"
        else "Use this code to reset your password:"
    )

    message = (
        f"{action_text}\n\n"
        f"    {otp}\n\n"
        f"This code expires in {expiry_minutes} minute{'' if expiry_minutes == 1 else 's'}. "
        "If you didn't request this, you "
        "can safely ignore this email — your account is still secure."
    )

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[to_email],
            fail_silently=False,
        )
        return True
    except Exception:
        # Never let an email-delivery failure surface a 500 to the user or
        # block the OTP record that was already saved — the user can still
        # retry/resend, and this is loud in the logs for real ops visibility.
        logger.exception("Failed to send OTP email to %s", to_email)
        return False
