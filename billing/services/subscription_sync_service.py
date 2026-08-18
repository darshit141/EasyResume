"""Applies Stripe webhook events to our local Subscription record. This is
the only source of truth update for subscription state — the checkout flow
itself never marks a subscription active; only a confirmed webhook event
does, so a user can't grant themselves access by hitting the success URL
without actually paying.
"""

import logging
from datetime import datetime, timezone as dt_timezone

from billing.models import Subscription

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
        else:
            logger.info("Ignoring unhandled Stripe event type: %s", event_type)

    @staticmethod
    def _handle_checkout_completed(session):
        user_id = (session.get("metadata") or {}).get("user_id")
        plan_id = (session.get("metadata") or {}).get("plan_id")
        if not user_id:
            logger.warning("checkout.session.completed with no user_id metadata; ignoring.")
            return

        subscription = Subscription.objects.filter(user_id=user_id).first()
        if subscription is None:
            logger.warning("checkout.session.completed for unknown local user_id=%s", user_id)
            return

        subscription.stripe_customer_id = session.get("customer") or subscription.stripe_customer_id
        subscription.stripe_subscription_id = session.get("subscription") or subscription.stripe_subscription_id
        if plan_id:
            subscription.plan_id = plan_id
        subscription.status = "active"
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

        stripe_status = stripe_subscription.get("status")
        subscription.status = "active" if stripe_status in _ACTIVE_STRIPE_STATUSES else stripe_status
        subscription.stripe_subscription_id = stripe_subscription.get("id") or subscription.stripe_subscription_id

        period_end = stripe_subscription.get("current_period_end")
        if period_end:
            subscription.current_period_end = datetime.fromtimestamp(period_end, tz=dt_timezone.utc)

        subscription.save()

    @staticmethod
    def _handle_subscription_deleted(stripe_subscription):
        subscription = Subscription.objects.filter(
            stripe_customer_id=stripe_subscription.get("customer")
        ).first()
        if subscription is None:
            return
        subscription.status = "canceled"
        subscription.save(update_fields=["status", "updated_at"])
