"""All direct Stripe API calls live here — nothing else in the app imports
`stripe` directly. Every method fails fast and safely when Stripe isn't
configured (STRIPE_SECRET_KEY unset) rather than raising an opaque SDK
error, matching the same graceful-degradation pattern used for the
Groq/Gemini AI providers elsewhere in this backend.
"""

import stripe
from django.conf import settings

from billing.models import Subscription
from billing.plans import get_plan


class BillingError(Exception):
    code = "BILLING_ERROR"
    message = "A billing error occurred."

    def __init__(self, message=None):
        super().__init__(message or self.message)


class StripeNotConfiguredError(BillingError):
    code = "STRIPE_NOT_CONFIGURED"
    message = "Billing is not configured on this server yet."


class PlanNotFoundError(BillingError):
    code = "PLAN_NOT_FOUND"
    message = "That plan does not exist."


class PlanNotPurchasableError(BillingError):
    code = "PLAN_NOT_PURCHASABLE"
    message = "This plan is not yet available for purchase (no Stripe price configured)."


class NoStripeCustomerError(BillingError):
    code = "NO_STRIPE_CUSTOMER"
    message = "No billing history found for this account yet."


class StripeService:
    @staticmethod
    def _require_configured():
        if not settings.STRIPE_SECRET_KEY:
            raise StripeNotConfiguredError()
        stripe.api_key = settings.STRIPE_SECRET_KEY

    @staticmethod
    def get_or_create_customer(user, subscription):
        if subscription.stripe_customer_id:
            return subscription.stripe_customer_id

        customer = stripe.Customer.create(
            email=user.email,
            name=user.name,
            metadata={"user_id": str(user.id)},
        )
        subscription.stripe_customer_id = customer.id
        subscription.save(update_fields=["stripe_customer_id"])
        return customer.id

    @classmethod
    def create_checkout_session(cls, user, plan_id):
        cls._require_configured()

        plan = get_plan(plan_id)
        if plan is None:
            raise PlanNotFoundError()
        if not plan["stripe_price_id"]:
            raise PlanNotPurchasableError()

        subscription, _ = Subscription.objects.get_or_create(user=user)
        customer_id = cls.get_or_create_customer(user, subscription)

        session = stripe.checkout.Session.create(
            customer=customer_id,
            mode="subscription",
            line_items=[{"price": plan["stripe_price_id"], "quantity": 1}],
            success_url=f"{settings.FRONTEND_URL}/app/settings?checkout=success",
            cancel_url=f"{settings.FRONTEND_URL}/app/settings?checkout=canceled",
            client_reference_id=str(user.id),
            metadata={"user_id": str(user.id), "plan_id": plan_id},
        )
        return session.url

    @classmethod
    def create_portal_session(cls, user):
        cls._require_configured()

        subscription = Subscription.objects.filter(user=user).first()
        if not subscription or not subscription.stripe_customer_id:
            raise NoStripeCustomerError()

        portal = stripe.billing_portal.Session.create(
            customer=subscription.stripe_customer_id,
            return_url=f"{settings.FRONTEND_URL}/app/settings",
        )
        return portal.url

    @classmethod
    def construct_webhook_event(cls, payload, signature_header):
        cls._require_configured()
        if not settings.STRIPE_WEBHOOK_SECRET:
            raise StripeNotConfiguredError("Webhook secret is not configured.")
        return stripe.Webhook.construct_event(
            payload, signature_header, settings.STRIPE_WEBHOOK_SECRET
        )
