"""Forwards a bug report or feature request submitted from the app's
"Report a bug" button to the sales inbox. Unlike the billing dunning/receipt
emails (which log and swallow a delivery failure because the state change
they're announcing already happened), this email *is* the operation — if it
doesn't send, the report is lost with nothing else to fall back on, so a
delivery failure is raised rather than hidden.
"""

import logging

from django.conf import settings
from django.core.mail import EmailMessage

logger = logging.getLogger(__name__)

FEEDBACK_INBOX = "sales@patrixel.com"

CATEGORY_LABELS = {"bug": "Bug report", "feature": "Feature request", "contact": "Contact message"}


def send_feedback_email(*, category, message, reporter_email, reporter_name, page_url, user_id):
    label = CATEGORY_LABELS.get(category, category)
    subject = f"[IntelliResume] {label}"

    lines = [
        f"Category: {label}",
        f"From: {reporter_name or 'Anonymous'} <{reporter_email or 'no email given'}>",
    ]
    if user_id:
        lines.append(f"User ID: {user_id}")
    if page_url:
        lines.append(f"Page: {page_url}")
    lines.append("")
    lines.append(message)
    body = "\n".join(lines)

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[FEEDBACK_INBOX],
        # Replying to the notification reaches the reporter directly,
        # without anyone having to copy an address out of the body.
        reply_to=[reporter_email] if reporter_email else None,
    )
    email.send(fail_silently=False)
