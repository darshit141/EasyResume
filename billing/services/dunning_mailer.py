"""Customer-facing emails for a subscription charge's outcome — success or
failure. The failure side closes the dunning gap: the webhook already
logged the decline server-side, but nothing ever told the customer their
card was declined. Left unfixed, they find out only when Stripe's retries
exhaust and access silently disappears weeks later, which reads as a bug,
not a billing issue, and is a real avoidable-churn/support-ticket risk. The
success side is the receipt customers expect for a charge on their card or
UPI mandate — without it, a legitimate renewal looks unexplained on their
bank statement. Uses the same send_mail path as OTP delivery (see
authentication/utils/otp_mailer.py): console backend in dev, real SMTP once
EMAIL_HOST is set, no code change to go from one to the other.
"""

import logging

from django.conf import settings
from django.core.mail import send_mail

from billing.plans import CURRENCY_SYMBOLS, get_plan

logger = logging.getLogger(__name__)


def _format_amount(amount_minor, currency):
    """`amount_minor` is in the currency's smallest unit (cents/paise), the
    unit every processor's webhook payload actually uses."""
    symbol = CURRENCY_SYMBOLS.get(currency, currency + " ")
    return f"{symbol}{amount_minor / 100:,.2f}"


def _plan_label(plan_id, billing_period):
    plan = get_plan(plan_id)
    name = plan["name"] if plan else plan_id
    period = "year" if billing_period == "annual" else "month"
    return f"{name} ({period}ly)"


def send_payment_failed_email(
    user, invoice, processor="stripe", *, amount_minor=None, currency=None,
    plan_id=None, billing_period=None, reason=None,
):
    """`invoice` is the raw Stripe invoice object (or, for Razorpay, a small
    dict with at least an `id`) from the webhook payload. `hosted_invoice_url`
    is Stripe's own hosted page for updating a payment method and retrying
    the charge — the correct place to send someone, rather than building a
    bespoke "update your card" flow.

    For a Razorpay subscriber the renewal was a UPI AutoPay, card or bank
    mandate debit, and the things they can do about it are different — so
    is the email. Telling someone paying by UPI to "update your card" is the
    kind of message that gets a support ticket instead of a fix.

    `amount_minor`/`currency`/`plan_id`/`billing_period`/`reason` are the
    "necessary details" the customer needs to recognize which charge this
    is about and why it failed — optional because not every call site has
    all of them (a bare payment id, no enriched payload), and a delivered
    email with less detail beats no email at all.
    """

    hosted_invoice_url = invoice.get("hosted_invoice_url") or f"{settings.FRONTEND_URL}/app/settings"

    details_lines = []
    if plan_id:
        details_lines.append(f"Plan: {_plan_label(plan_id, billing_period)}")
    if amount_minor is not None and currency:
        details_lines.append(f"Amount due: {_format_amount(amount_minor, currency)}")
    if reason:
        details_lines.append(f"Reason given by the bank: {reason}")
    details_block = ("\n".join(details_lines) + "\n\n") if details_lines else ""

    subject = "Your IntelliResume payment didn't go through"
    if processor == "razorpay":
        message = (
            f"Hi {user.name},\n\n"
            "We couldn't collect your IntelliResume renewal. It was due to be "
            "paid automatically through the UPI AutoPay, card or bank mandate "
            "you set up. Your access hasn't been affected yet — the payment "
            "will be retried automatically over the next few days.\n\n"
            f"{details_block}"
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
            f"{details_block}"
            "To avoid any interruption, update your payment method here:\n\n"
            f"    {hosted_invoice_url}\n\n"
            "If this keeps failing, your subscription will eventually be "
            "marked past due and Pro features will become unavailable until "
            "it's resolved.\n\n"
            "If you believe this is a mistake, or your card is already up to "
            "date, feel free to ignore this — it may just be a temporary issue "
            "on your bank's side."
        )

    return _send(subject, message, user.email, "payment-failed")


def send_payment_succeeded_email(
    user, *, amount_minor, currency, plan_id, billing_period, reference, processor="stripe",
):
    """The receipt for a successful subscription charge — first payment or
    renewal alike. `reference` is whatever the processor calls this charge
    (Stripe invoice id, Razorpay/GST invoice number) so a customer can
    match it to their bank statement or ask about it by that number.
    """

    subject = "Your IntelliResume payment was successful"
    method = "UPI AutoPay, card or bank mandate" if processor == "razorpay" else "card"
    message = (
        f"Hi {user.name},\n\n"
        f"We've received your IntelliResume payment. Thanks for staying subscribed!\n\n"
        f"Plan: {_plan_label(plan_id, billing_period)}\n"
        f"Amount charged: {_format_amount(amount_minor, currency)}\n"
        f"Charged to: {method}\n"
        f"Reference: {reference}\n\n"
        "You can review your billing details and past invoices any time from:\n\n"
        f"    {settings.FRONTEND_URL}/app/settings\n\n"
        "If you didn't expect this charge, please get in touch."
    )

    return _send(subject, message, user.email, "payment-succeeded")


def _send(subject, message, to_email, kind):
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
        # Never let email delivery failure break webhook processing — the
        # webhook's own job (syncing subscription state) already succeeded;
        # losing the notification is unfortunate but must not turn into a
        # 500 that makes Stripe/Razorpay retry an event that was otherwise
        # handled.
        logger.exception("Failed to send %s email to %s", kind, to_email)
        return False
