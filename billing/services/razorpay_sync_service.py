"""Applies Razorpay subscription state to our local Subscription row, and
issues a GST invoice for every successful rupee charge.

The Razorpay twin of subscription_sync_service. Two rules shape it:

  * Razorpay says outright that webhooks may arrive out of order. Every
    event carries the time Razorpay created it, and an event older than the
    last one applied is recorded but not applied — otherwise a delayed
    "charged" arriving after "cancelled" would reactivate a subscription the
    customer had ended from their UPI app.

  * The subscription entity inside the event is the source of truth for
    state. We never infer state from the event *name* alone; the name only
    decides which side effects (a dunning email, an invoice) go with it.
"""

import logging

from django.db import transaction
from django.utils import timezone

from billing.models import GSTInvoice, InvoiceSequence, Subscription
from billing.plans import PLANS_BY_ID, PROCESSOR_RAZORPAY, find_plan_by_razorpay_plan_id

logger = logging.getLogger(__name__)

# Razorpay subscription status -> our Subscription.status.
#   pending   a renewal debit failed and Razorpay is retrying. Access is kept
#             while it retries — the same grace the Stripe path gives while
#             Stripe retries a card, and what the dunning email promises.
#   halted    every retry failed; the customer must act. Access ends.
#   paused    paused (we never pause, but a dashboard action can). Access ends.
# `past_due` is what the rest of the app already understands as "payment
# trouble, no access".
_STATUS_MAP = {
    "created": "incomplete",
    "authenticated": "active",
    "active": "active",
    "pending": "active",
    "halted": "past_due",
    "paused": "past_due",
    "cancelled": "canceled",
    "completed": "canceled",
    "expired": "canceled",
}


class RazorpaySyncService:
    @staticmethod
    def apply_event(event, event_id=""):
        event_type = event.get("event", "")
        payload = event.get("payload") or {}
        entity = ((payload.get("subscription") or {}).get("entity")) or {}
        payment = ((payload.get("payment") or {}).get("entity")) or {}
        event_at = _event_time(event)

        if event_type.startswith("subscription.") and entity:
            with transaction.atomic():
                subscription = RazorpaySyncService.apply_subscription(entity, event_at=event_at)
            if subscription is None:
                return

            if event_type == "subscription.charged" and payment:
                RazorpaySyncService.issue_invoice_for_payment(subscription, entity, payment)
            elif event_type == "subscription.pending":
                RazorpaySyncService._notify_payment_failed(subscription, entity, payment)
            return

        if event_type == "refund.processed":
            RazorpaySyncService._handle_refund(payload)
            return

        logger.info("Ignoring unhandled Razorpay event type: %s", event_type)

    # -- subscription state --------------------------------------------

    @staticmethod
    def _find_local(entity, user_id=None):
        razorpay_id = entity.get("id") or ""
        subscription = (
            Subscription.objects.select_for_update()
            .filter(razorpay_subscription_id=razorpay_id)
            .first()
            if razorpay_id
            else None
        )
        if subscription is not None:
            return subscription

        # The row may carry a newer, abandoned checkout's id while an older
        # payment link was the one actually paid. The note we attached at
        # creation says whose it is.
        owner = user_id or (entity.get("notes") or {}).get("user_id")
        if not owner:
            return None
        return Subscription.objects.select_for_update().filter(user_id=owner).first()

    @staticmethod
    def apply_subscription(entity, event_at=None, user_id=None):
        subscription = RazorpaySyncService._find_local(entity, user_id=user_id)
        if subscription is None:
            logger.warning(
                "Razorpay subscription %s matches no local account; ignoring.", entity.get("id")
            )
            return None

        # A Stripe subscriber's row is not ours to change. This can only
        # happen if someone paid an old rupee link after moving to a card
        # subscription in euros, and the right outcome is a human looking at
        # it, not two processors fighting over one row.
        if subscription.payment_processor == "stripe" and subscription.is_active:
            logger.error(
                "Razorpay subscription %s arrived for user_id=%s, who has an active Stripe "
                "subscription. Not applied; resolve manually.",
                entity.get("id"), subscription.user_id,
            )
            return None

        if (
            event_at is not None
            and subscription.processor_event_at is not None
            and event_at < subscription.processor_event_at
        ):
            logger.info(
                "Skipping out-of-order Razorpay event for %s (event %s, last applied %s).",
                entity.get("id"), event_at, subscription.processor_event_at,
            )
            return subscription

        razorpay_status = entity.get("status", "")
        subscription.status = _STATUS_MAP.get(razorpay_status, subscription.status)
        subscription.payment_processor = PROCESSOR_RAZORPAY
        subscription.razorpay_subscription_id = entity.get("id") or subscription.razorpay_subscription_id
        subscription.razorpay_customer_id = entity.get("customer_id") or subscription.razorpay_customer_id
        subscription.currency = "INR"

        resolved = find_plan_by_razorpay_plan_id(entity.get("plan_id"))
        notes = entity.get("notes") or {}
        if resolved:
            subscription.plan_id, _, subscription.billing_period = resolved
        elif notes.get("plan_id") in PLANS_BY_ID:
            # The plan id wasn't found in the catalog (renamed in the
            # dashboard, say), but our own note at checkout still says what
            # was bought.
            subscription.plan_id = notes["plan_id"]
            subscription.billing_period = notes.get("billing_period") or subscription.billing_period

        from billing.services.razorpay_service import unix_to_datetime

        period_end = unix_to_datetime(entity.get("current_end"))
        if period_end is not None:
            subscription.current_period_end = period_end
        elif subscription.status == "active" and subscription.current_period_end is None:
            # "authenticated" can arrive before Razorpay has stamped the
            # first cycle's dates. Access must not be open-ended in the
            # meantime, so it runs to the end of one period and the next
            # event corrects it.
            subscription.current_period_end = _one_period_from_now(subscription.billing_period)

        # A cancellation scheduled for cycle end leaves Razorpay's status
        # "active" until the cycle is over (RazorpayService.cancel_for_user
        # sets the flag); once it has actually ended there is nothing left
        # pending.
        if razorpay_status in ("cancelled", "completed", "expired"):
            subscription.cancel_at_period_end = False
        if event_at is not None:
            subscription.processor_event_at = event_at

        subscription.save()
        return subscription

    # -- invoices -------------------------------------------------------

    @staticmethod
    def issue_invoice_for_payment(subscription, entity, payment):
        """One invoice per captured payment, however many times we're told."""
        payment_id = payment.get("id") or ""
        if not payment_id or payment.get("status") not in ("captured", None):
            return None
        if (payment.get("currency") or "INR") != "INR":
            return None

        existing = GSTInvoice.objects.filter(razorpay_payment_id=payment_id).first()
        if existing is not None:
            return existing

        amount = int(payment.get("amount") or 0)
        if amount <= 0:
            return None

        from billing.services.gst_invoice_service import GSTInvoiceService

        issued_at = _payment_time(payment)
        invoice = GSTInvoiceService.issue(
            user=subscription.user,
            payment_id=payment_id,
            razorpay_subscription_id=entity.get("id") or subscription.razorpay_subscription_id,
            amount_paise=amount,
            plan_id=subscription.plan_id,
            billing_period=subscription.billing_period or "monthly",
            issued_at=issued_at,
        )

        from billing.services.dunning_mailer import send_payment_succeeded_email

        send_payment_succeeded_email(
            subscription.user,
            amount_minor=invoice.total,
            currency=invoice.currency,
            plan_id=subscription.plan_id,
            billing_period=subscription.billing_period or "monthly",
            reference=invoice.number,
            processor="razorpay",
        )

        return invoice

    # -- side effects ---------------------------------------------------

    @staticmethod
    def _notify_payment_failed(subscription, entity, payment):
        from billing.services.dunning_mailer import send_payment_failed_email

        amount = payment.get("amount")
        reason = payment.get("error_description") or None

        send_payment_failed_email(
            subscription.user,
            {"id": payment.get("id") or entity.get("id")},
            processor="razorpay",
            amount_minor=amount if amount else None,
            currency=(payment.get("currency") or "INR") if amount else None,
            plan_id=subscription.plan_id,
            billing_period=subscription.billing_period,
            reason=reason,
        )
        logger.warning(
            "Razorpay renewal failed for user_id=%s subscription=%s",
            subscription.user_id, entity.get("id"),
        )

    @staticmethod
    def _handle_refund(payload):
        """A full refund of a subscription payment ends access, matching the
        Stripe charge.refunded rule. A partial refund is a goodwill credit
        and changes nothing."""
        refund = ((payload.get("refund") or {}).get("entity")) or {}
        payment = ((payload.get("payment") or {}).get("entity")) or {}
        payment_id = refund.get("payment_id") or payment.get("id")
        if not payment_id:
            return

        invoice = GSTInvoice.objects.filter(razorpay_payment_id=payment_id).first()
        if invoice is None or not invoice.razorpay_subscription_id:
            return

        amount = int(payment.get("amount") or invoice.total)
        refunded = int(payment.get("amount_refunded") or refund.get("amount") or 0)
        if amount <= 0 or refunded < amount:
            logger.info("Partial Razorpay refund on %s; access unchanged.", payment_id)
            return

        updated = Subscription.objects.filter(
            razorpay_subscription_id=invoice.razorpay_subscription_id
        ).update(status="canceled", updated_at=timezone.now())
        if updated:
            logger.info("Full Razorpay refund on %s; access ended.", payment_id)


def _event_time(event):
    from billing.services.razorpay_service import unix_to_datetime

    return unix_to_datetime(event.get("created_at"))


def _payment_time(payment):
    from billing.services.razorpay_service import unix_to_datetime

    return unix_to_datetime(payment.get("created_at")) or timezone.now()


def _one_period_from_now(billing_period):
    from datetime import timedelta

    days = 366 if billing_period == "annual" else 31
    return timezone.now() + timedelta(days=days)


def next_invoice_number(prefix, financial_year):
    """Reserve the next serial for a financial year. Must be called inside a
    transaction; the row lock is what keeps two concurrent charges from
    being handed the same number."""
    from billing.gst import format_invoice_number

    sequence, _ = InvoiceSequence.objects.select_for_update().get_or_create(
        financial_year=financial_year
    )
    sequence.last_number += 1
    sequence.save(update_fields=["last_number"])
    return format_invoice_number(prefix, financial_year, sequence.last_number)
