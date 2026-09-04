"""Email delivery over Resend's HTTP API instead of SMTP.

This exists because of a hard infrastructure constraint, not a preference:
DigitalOcean blocks outbound SMTP (ports 25/465/587) on its droplets to
prevent spam abuse. Verified on this droplet - connections to
smtp.hostinger.com on both 465 and 587 time out from the host itself, not
just from inside a container. Every SMTP provider fails identically there,
so no choice of provider or credentials can fix it.

Resend's HTTP API runs over 443, which is not blocked, so it sidesteps the
restriction entirely.

Being a real Django email backend means nothing else has to know. OTP
codes, dunning notices and outcome nudges all keep calling send_mail() and
transparently go out over HTTPS instead.
"""

import base64
import json
import logging

import requests
from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

logger = logging.getLogger(__name__)

RESEND_ENDPOINT = "https://api.resend.com/emails"
REQUEST_TIMEOUT_SECONDS = 15


class ResendAPIEmailBackend(BaseEmailBackend):
    """Sends each message through Resend's REST API.

    Honours Django's `fail_silently` contract: when False, a delivery
    failure raises, which is what lets callers like the OTP mailer log a
    real error rather than believing a code was sent when it was not.
    """

    def __init__(self, fail_silently=False, **kwargs):
        super().__init__(fail_silently=fail_silently, **kwargs)
        self.api_key = getattr(settings, "RESEND_API_KEY", "") or ""

    def send_messages(self, email_messages):
        if not email_messages:
            return 0

        if not self.api_key:
            # Silently "succeeding" here would be the worst outcome: callers
            # would believe a login code went out when nothing was sent.
            if not self.fail_silently:
                raise ValueError(
                    "RESEND_API_KEY is not configured, so no email can be sent."
                )
            logger.error("RESEND_API_KEY is not configured; dropped %s message(s).", len(email_messages))
            return 0

        sent = 0
        for message in email_messages:
            if self._send(message):
                sent += 1
        return sent

    def _send(self, message):
        recipients = list(message.to or [])
        if not recipients:
            return False

        payload = {
            "from": message.from_email or settings.DEFAULT_FROM_EMAIL,
            "to": recipients,
            "subject": message.subject or "",
            "text": message.body or "",
        }
        if message.cc:
            payload["cc"] = list(message.cc)
        if message.bcc:
            payload["bcc"] = list(message.bcc)
        if message.reply_to:
            payload["reply_to"] = list(message.reply_to)

        attachments = _encode_attachments(message)
        if attachments:
            payload["attachments"] = attachments

        try:
            response = requests.post(
                RESEND_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                data=json.dumps(payload),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            logger.exception("Resend API request failed.")
            if not self.fail_silently:
                raise
            return False

        if response.status_code >= 400:
            # Resend's body explains *why* (unverified domain, invalid
            # recipient, quota), which is the difference between a
            # debuggable failure and a mystery.
            detail = response.text[:500]
            logger.error(
                "Resend API rejected the message: HTTP %s %s", response.status_code, detail
            )
            if not self.fail_silently:
                raise RuntimeError(f"Resend API error {response.status_code}: {detail}")
            return False

        return True


def _encode_attachments(message):
    """Translate Django's attachments into Resend's base64 form.

    Without this, `EmailMessage.attach(...)` would be accepted by Django and
    then silently dropped on the way out — an application email would arrive
    with no resume attached and nothing would look broken. Sending a job
    application without its attachment is worse than failing to send it, so
    unreadable attachments raise rather than being skipped.
    """
    encoded = []
    for attachment in getattr(message, "attachments", None) or []:
        if isinstance(attachment, (list, tuple)):
            filename, content = attachment[0], attachment[1]
        else:
            # A MIMEBase object, as produced by attaching a pre-built part.
            filename = attachment.get_filename()
            content = attachment.get_payload(decode=True)

        if not filename:
            raise ValueError("Cannot send an attachment without a filename.")

        if isinstance(content, str):
            content = content.encode("utf-8")

        encoded.append(
            {
                "filename": filename,
                "content": base64.b64encode(content).decode("ascii"),
            }
        )
    return encoded
