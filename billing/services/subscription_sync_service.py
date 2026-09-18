"""Applies Stripe webhook events to our local Subscription record. This is
the only source of truth update for subscription state — the checkout flow
itself never marks a subscription active; only a confirmed webhook event
does, so a user can't grant themselves access by hitting the success URL
without actually paying.
"""

import logging
from datetime import datetime, timezone as dt_timezone

from billing.models import Subscription
from billing.plans import find_plan_by_stripe_price_id

logger = logging.getLogger(__name__)

# Stripe subscription statuses that should be reflected as "active" locally.
# "trialing" is included so a configured trial period grants access too.
_ACTIVE_STRIPE_STATUSES = {"active", "trialing"}


class SubscriptionSyncService:
    @staticmethod
    def apply_event(event):
        event_type = event["type"]
        data = event["data"]["object"]

        if event_type == "checkout.session.completed":
            SubscriptionSyncService._handle_checkout_completed(data)
        elif event_type in ("customer.subscription.updated", "customer.subscription.created"):
            SubscriptionSyncService._handle_subscription_updated(data)
        elif event_type == "customer.subscription.deleted":
            SubscriptionSyncService._handle_subscription_deleted(data)
        elif event_type == "invoice.payment_failed":
            SubscriptionSyncService._handle_invoice_payment_failed(data)
        elif event_type == "invoice.paid":
            SubscriptionSyncService._handle_invoice_paid(data)
        elif event_type == "charge.refunded":
            SubscriptionSyncService._handle_charge_refunded(data)
        else:
            logger.info("Ignoring unhandled Stripe event type: %s", event_type)

    @staticmethod
    def _handle_checkout_completed(session):
        metadata = session.get("metadata") or {}
        user_id = metadata.get("user_id")
        plan_id = metadata.get("plan_id")
        if not user_id:
            logger.warning("checkout.session.completed with no user_id metadata; ignoring.")
            return

        subscription = Subscription.objects.filter(user_id=user_id).first()
        if subscription is None:
            logger.warning("checkout.session.completed for unknown local user_id=%s", user_id)
            return

        subscription.stripe_customer_id = session.get("customer") or subscription.stripe_customer_id
        subscription.payment_processor = "stripe"
        subscription.cancel_at_period_end = False
        stripe_subscription_id = session.get("subscription") or subscription.stripe_subscription_id
        subscription.stripe_subscription_id = stripe_subscription_id
        if plan_id:
            subscription.plan_id = plan_id
        # Set from the checkout session's own metadata (see
        # StripeService.create_checkout_session) — this is what makes MRR
        # computable at all; without it there's no way to know which price
        # a given active subscriber is actually paying.
        if metadata.get("currency"):
            subscription.currency = metadata["currency"]
        if metadata.get("billing_period"):
            subscription.billing_period = metadata["billing_period"]
        subscription.status = "active"

        # Fetch the subscription directly rather than waiting for a
        # separate customer.subscription.* webhook — without this, the
        # first billing period had no recorded expiry until that later
        # event happened to arrive (COMMERCIAL_READINESS_AUDIT.md B7).
        if stripe_subscription_id:
            try:
                from billing.services.stripe_service import StripeService

                stripe_subscription = StripeService.get_subscription(stripe_subscription_id)
                period_end = stripe_subscription.get("current_period_end")
                if period_end:
                    subscription.current_period_end = datetime.fromtimestamp(period_end, tz=dt_timezone.utc)
            except Exception:
                logger.exception(
                    "Failed to fetch subscription %s for period_end; a later "
                    "customer.subscription.updated event will still fill it in.",
                    stripe_subscription_id,
                )

        subscription.save()

    @staticmethod
    def _handle_subscription_updated(stripe_subscription):
        subscription = Subscription.objects.filter(
            stripe_customer_id=stripe_subscription.get("customer")
        ).first()
        if subscription is None:
            logger.warning(
                "subscription.updated for unknown stripe_customer_id=%s",
                stripe_subscription.get("customer"),
            )
            return
        if _billed_elsewhere(subscription):
            return

        stripe_status = stripe_subscription.get("status")
        subscription.status = "active" if stripe_status in _ACTIVE_STRIPE_STATUSES else stripe_status
        subscription.stripe_subscription_id = stripe_subscription.get("id") or subscription.stripe_subscription_id

        period_end = stripe_subscription.get("current_period_end")
        if period_end:
            subscription.current_period_end = datetime.fromtimestamp(period_end, tz=dt_timezone.utc)
        subscription.cancel_at_period_end = bool(stripe_subscription.get("cancel_at_period_end"))

        # A plan or billing-period change made through the Stripe Customer
        # Portal (the only upgrade/downgrade path this app offers — see
        # CreatePortalSessionAPIView) lands here as a subscription.updated
        # event with a new price on the first item, not as a new checkout.
        # Without re-deriving plan_id/currency/billing_period from it, a
        # monthly->annual switch (or any future second paid plan) would
        # leave this record frozen at whatever the original checkout set,
        # silently corrupting MRR/ARR reporting from that point on.
        items = (stripe_subscription.get("items") or {}).get("data") or []
        if items:
            price = (items[0] or {}).get("price") or {}
            resolved = find_plan_by_stripe_price_id(price.get("id"))
            if resolved:
                plan_id, currency, billing_period = resolved
                subscription.plan_id = plan_id
                subscription.currency = currency
                subscription.billing_period = billing_period

        subscription.save()

    @staticmethod
    def _handle_subscription_deleted(stripe_subscription):
        subscription = Subscription.objects.filter(
            stripe_customer_id=stripe_subscription.get("customer")
        ).first()
        if subscription is None or _billed_elsewhere(subscription):
            return
        subscription.status = "canceled"
        subscription.cancel_at_period_end = False
        subscription.save(update_fields=["status", "cancel_at_period_end", "updated_at"])

    @staticmethod
    def _handle_invoice_payment_failed(invoice):
        """A renewal charge failed. Stripe's own retry schedule (Smart
        Retries, if enabled) will keep trying; this just makes the failure
        visible locally right away rather than waiting for Stripe to give
        up and eventually send customer.subscription.updated with a
        past_due/unpaid status. Never revokes access here — Stripe's
        subscription status transition (surfaced via
        customer.subscription.updated) remains the actual source of truth
        for whether access should be cut off."""
        subscription = Subscription.objects.filter(
            stripe_customer_id=invoice.get("customer")
        ).first()
        if subscription is None:
            return

        from billing.services.dunning_mailer import send_payment_failed_email

        send_payment_failed_email(subscription.user, invoice)

        logger.warning(
            "Invoice payment failed for user_id=%s stripe_customer_id=%s invoice=%s",
            subscription.user_id,
            invoice.get("customer"),
            invoice.get("id"),
        )

    @staticmethod
    def _handle_invoice_paid(invoice):
        """A renewal (or the first payment) succeeded. If the subscription
        had been marked past_due by an earlier failed attempt, this is the
        recovery signal — restore active status immediately rather than
        waiting for a separate customer.subscription.updated event."""
        subscription = Subscription.objects.filter(
            stripe_customer_id=invoice.get("customer")
        ).first()
        if subscription is None or _billed_elsewhere(subscription):
            return
        if subscription.status == "past_due":
            subscription.status = "active"
            subscription.save(update_fields=["status", "updated_at"])

    @staticmethod
    def _handle_charge_refunded(charge):
        """A full refund on a subscription charge is treated as the
        customer no longer having paid for the current period — access is
        revoked. A partial refund (goodwill credit, proration adjustment)
        is logged only; it doesn't by itself mean access should end."""
        subscription = Subscription.objects.filter(
            stripe_customer_id=charge.get("customer")
        ).first()
        if subscription is None or _billed_elsewhere(subscription):
            return

        amount = charge.get("amount") or 0
        amount_refunded = charge.get("amount_refunded") or 0
        is_full_refund = amount > 0 and amount_refunded >= amount

        logger.info(
            "Charge refunded for user_id=%s stripe_customer_id=%s amount_refunded=%s full=%s",
            subscription.user_id,
            charge.get("customer"),
            amount_refunded,
            is_full_refund,
        )
        if is_full_refund:
            subscription.status = "canceled"
            subscription.save(update_fields=["status", "updated_at"])


def _billed_elsewhere(subscription):
    """True when this account now pays through Razorpay.

    A customer who once had a Stripe subscription keeps their Stripe
    customer id after moving to a rupee plan, so a late Stripe event about
    the old subscription still finds their row. It must not be allowed to
    cancel, reactivate or re-plan a subscription Stripe knows nothing about.
    """
    if subscription.payment_processor == "razorpay":
        logger.info(
            "Ignoring Stripe event for user_id=%s, who is now billed through Razorpay.",
            subscription.user_id,
        )
        return True
    return False
