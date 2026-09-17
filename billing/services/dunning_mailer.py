"""Customer-facing email for a failed renewal charge — the dunning gap: the
webhook already logged this server-side, but nothing ever told the customer
their card was declined. Left unfixed, they find out only when Stripe's
retries exhaust and access silently disappears weeks later, which reads as
a bug, not a billing issue, and is a real avoidable-churn/support-ticket
risk. Uses the same send_mail path as OTP delivery (see
authentication/utils/otp_mailer.py): console backend in dev, real SMTP once
EMAIL_HOST is set, no code change to go from one to the other.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def send_payment_failed_email(user, invoice, processor="stripe"):
    """`invoice` is the raw Stripe invoice object from the webhook payload.
    `hosted_invoice_url` is Stripe's own hosted page for updating a payment
    method and retrying the charge — the correct place to send someone,
    rather than building a bespoke "update your card" flow.

    For a Razorpay subscriber the renewal was a UPI AutoPay, card or bank
    mandate debit, and the things they can do about it are different — so
    is the email. Telling someone paying by UPI to "update your card" is the
    kind of message that gets a support ticket instead of a fix.
    """

    hosted_invoice_url = invoice.get("hosted_invoice_url") or f"{settings.FRONTEND_URL}/app/settings"

    subject = "Your IntelliResume payment didn't go through"
    if processor == "razorpay":
        message = (
            f"Hi {user.name},\n\n"
            "We couldn't collect your IntelliResume renewal. It was due to be "
            "paid automatically through the UPI AutoPay, card or bank mandate "
            "you set up. Your access hasn't been affected yet — the payment "
            "will be retried automatically over the next few days.\n\n"
            "The usual causes are quick to fix:\n"
            "  - not enough balance in the linked account on the retry day\n"
            "  - the AutoPay mandate was paused or revoked in your UPI app\n"
            "  - the card on the mandate has expired\n\n"
            "If the retries don't succeed, Pro features will pause until you "
            "subscribe again, which you can do any time from:\n\n"
            f"    {settings.FRONTEND_URL}/pricing\n\n"
            "If you've already sorted this out, you can ignore this email."
        )
    else:
        message = (
            f"Hi {user.name},\n\n"
            "We tried to charge your card for your IntelliResume subscription "
            "renewal, but the payment didn't go through. Your access hasn't "
            "been affected yet — Stripe will automatically retry the charge a "
            "few times over the next several days.\n\n"
            "To avoid any interruption, update your payment method here:\n\n"
            f"    {hosted_invoice_url}\n\n"
            "If this keeps failing, your subscription will eventually be "
            "marked past due and Pro features will become unavailable until "
            "it's resolved.\n\n"
            "If you believe this is a mistake, or your card is already up to "
            "date, feel free to ignore this — it may just be a temporary issue "
            "on your bank's side."
        )

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        return True
    except Exception:
        # Never let email delivery failure break webhook processing — the
        # webhook's own job (syncing subscription state) already succeeded;
        # losing the notification is unfortunate but must not turn into a
        # 500 that makes Stripe retry an event that was otherwise handled.
        logger.exception("Failed to send payment-failed email to %s", user.email)
        return False
