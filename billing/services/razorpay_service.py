"""All direct Razorpay API calls live here, as all Stripe calls live in
stripe_service. Nothing else in the app talks to Razorpay.

Razorpay is used for one thing: INR subscriptions, paid by UPI AutoPay, card
or bank e-mandate. The flow mirrors Stripe's in shape so the rest of the app
needs to know as little as possible about which processor is in play:

  1. `start_checkout` creates a Razorpay Subscription and hands the browser
     what Razorpay Checkout needs to open.
  2. The customer authorises the mandate in their UPI app (or with a card or
     netbanking) inside Razorpay's own checkout.
  3. `confirm_checkout` verifies Razorpay's signature on the result *and*
     re-reads the subscription from Razorpay's API before granting access.
     The browser's word alone is never enough — the same rule the Stripe
     flow follows by only ever activating from a verified webhook.
  4. Webhooks (razorpay_sync_service) keep the subscription in step for the
     rest of its life: renewals, failed debits, cancellations from the
     customer's UPI app, refunds.
"""

import hashlib
import hmac
import logging
import time

import requests
from django.conf import settings
from django.db import transaction

from billing.models import Subscription
from billing.plans import (
    PROCESSOR_RAZORPAY,
    UPI_AUTOPAY_MAX_PAISE,
    get_plan,
    get_price,
)
from billing.services.stripe_service import (
    BillingError,
    PlanNotFoundError,
    PlanNotPurchasableError,
)

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 20

# Razorpay subscription statuses that mean "this customer has paid for the
# current period". `authenticated` is included because we never pass
# `start_at`: without it Razorpay takes the first cycle's payment as part of
# authorising the mandate, so an authenticated subscription is a paid one.
ACTIVE_STATUSES = frozenset({"authenticated", "active"})


class RazorpayNotConfiguredError(BillingError):
    code = "RAZORPAY_NOT_CONFIGURED"
    message = "Payments in rupees aren't available on this server yet."


class RazorpayAPIError(BillingError):
    code = "RAZORPAY_ERROR"
    message = "The payment provider couldn't be reached. Please try again in a moment."


class AlreadySubscribedError(BillingError):
    code = "ALREADY_SUBSCRIBED"
    message = "You already have an active plan. Manage it from Settings."


class BillingStateRequiredError(BillingError):
    code = "BILLING_STATE_REQUIRED"
    message = "Choose the state you're billed in before paying — it decides how GST is shown on your invoice."


class PaymentVerificationError(BillingError):
    code = "PAYMENT_NOT_VERIFIED"
    message = "We couldn't verify that payment. If you were charged, it will be applied automatically within a few minutes."


class RazorpayService:
    # -- plumbing ---------------------------------------------------------

    @staticmethod
    def configured():
        return bool(settings.RAZORPAY_KEY_ID and settings.RAZORPAY_KEY_SECRET)

    @classmethod
    def _require_configured(cls):
        if not cls.configured():
            raise RazorpayNotConfiguredError()

    @classmethod
    def _request(cls, method, path, payload=None):
        cls._require_configured()
        url = f"{settings.RAZORPAY_API_BASE_URL.rstrip('/')}/{path.lstrip('/')}"
        try:
            response = requests.request(
                method,
                url,
                json=payload,
                auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET),
                timeout=_TIMEOUT_SECONDS,
            )
        except requests.RequestException as exc:
            logger.warning("Razorpay %s %s failed: %s", method, path, exc.__class__.__name__)
            raise RazorpayAPIError() from exc

        if response.status_code >= 400:
            # Razorpay's error description is safe to log (it never contains
            # credentials) and is the only way to diagnose a misconfigured
            # plan id. It is not shown to the customer.
            try:
                description = response.json().get("error", {}).get("description", "")
            except ValueError:
                description = ""
            logger.error(
                "Razorpay %s %s returned %s: %s", method, path, response.status_code, description
            )
            raise RazorpayAPIError()

        try:
            return response.json()
        except ValueError as exc:
            raise RazorpayAPIError() from exc

    # -- API --------------------------------------------------------------

    @classmethod
    def create_subscription(cls, razorpay_plan_id, total_count, notes):
        return cls._request(
            "POST",
            "subscriptions",
            {
                "plan_id": razorpay_plan_id,
                "total_count": total_count,
                "quantity": 1,
                # Razorpay's own emails and SMS (including the RBI-mandated
                # pre-debit notice) go to the customer.
                "customer_notify": 1,
                "notes": notes,
            },
        )

    @classmethod
    def fetch_subscription(cls, razorpay_subscription_id):
        return cls._request("GET", f"subscriptions/{razorpay_subscription_id}")

    @classmethod
    def fetch_payment(cls, payment_id):
        return cls._request("GET", f"payments/{payment_id}")

    @classmethod
    def cancel_subscription(cls, razorpay_subscription_id, at_cycle_end=False):
        """At cycle end for a customer who cancels (they keep what they paid
        for); immediately for account deletion, where there is no account
        left to keep access on."""
        return cls._request(
            "POST",
            f"subscriptions/{razorpay_subscription_id}/cancel",
            {"cancel_at_cycle_end": 1 if at_cycle_end else 0},
        )

    # -- signatures -------------------------------------------------------

    @staticmethod
    def _hmac_hex(secret, message):
        return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()

    @classmethod
    def verify_webhook_signature(cls, raw_body, signature):
        """HMAC-SHA256 of the *raw* request body with the webhook secret.

        Compared in constant time. A missing secret fails closed: an
        unverifiable webhook must never be allowed to grant access.
        """
        secret = settings.RAZORPAY_WEBHOOK_SECRET
        if not secret or not signature:
            return False
        return hmac.compare_digest(cls._hmac_hex(secret, raw_body), signature)

    @classmethod
    def verify_checkout_signature(cls, payment_id, razorpay_subscription_id, signature):
        """Razorpay signs a subscription checkout result as
        HMAC-SHA256("<payment_id>|<subscription_id>") with the API key
        secret. The order matters and differs from one-off order payments."""
        secret = settings.RAZORPAY_KEY_SECRET
        if not secret or not signature or not payment_id or not razorpay_subscription_id:
            return False
        message = f"{payment_id}|{razorpay_subscription_id}".encode("utf-8")
        return hmac.compare_digest(cls._hmac_hex(secret, message), signature)

    # -- checkout ---------------------------------------------------------

    @staticmethod
    def _total_count(billing_period):
        years = max(1, int(settings.RAZORPAY_SUBSCRIPTION_YEARS))
        return years * 12 if billing_period == "monthly" else years

    @classmethod
    def start_checkout(cls, user, plan_id, billing_period="monthly"):
        cls._require_configured()

        plan = get_plan(plan_id)
        if plan is None:
            raise PlanNotFoundError()
        if not plan.get("published"):
            raise BillingError("That plan isn't available.")

        price = get_price(plan_id, "INR", billing_period)
        if not price or price.get("processor") != PROCESSOR_RAZORPAY or not price["processor_price_id"]:
            raise PlanNotPurchasableError()
        if price["amount_cents"] > UPI_AUTOPAY_MAX_PAISE:
            # Not a customer's problem to solve, so the log says what to fix.
            logger.error(
                "INR %s %s is priced above the UPI AutoPay limit; renewals over UPI would fail.",
                plan_id, billing_period,
            )

        from billing.models import BillingProfile

        profile = BillingProfile.objects.filter(user=user).first()
        if profile is None or not profile.state_code:
            raise BillingStateRequiredError()

        subscription, _ = Subscription.objects.get_or_create(user=user)
        if subscription.is_active and subscription.payment_processor:
            # Checking out again would open a second mandate and bill twice.
            raise AlreadySubscribedError()

        # A checkout the customer walked away from leaves a Razorpay
        # subscription waiting in "created". Cancel it before opening a new
        # one, so an old payment link finished later can't start a second
        # mandate alongside this one.
        stale_id = subscription.razorpay_subscription_id
        if stale_id and not subscription.is_active:
            try:
                existing = cls.fetch_subscription(stale_id)
                if existing.get("status") == "created":
                    cls.cancel_subscription(stale_id)
            except BillingError:
                logger.warning("Could not cancel abandoned Razorpay subscription %s.", stale_id)

        created = cls.create_subscription(
            price["processor_price_id"],
            cls._total_count(billing_period),
            notes={
                "user_id": str(user.id),
                "plan_id": plan_id,
                "currency": "INR",
                "billing_period": billing_period,
            },
        )
        subscription.razorpay_subscription_id = created["id"]
        subscription.save(update_fields=["razorpay_subscription_id", "updated_at"])

        return {
            "processor": PROCESSOR_RAZORPAY,
            "checkout_url": created.get("short_url", ""),
            "razorpay": {
                # The key id is public by design — it is what identifies the
                # merchant to Checkout. The secret never leaves the server.
                "key_id": settings.RAZORPAY_KEY_ID,
                "subscription_id": created["id"],
                "name": "IntelliResume",
                "description": f"{plan['name']} — {'monthly' if billing_period == 'monthly' else 'annual'}",
                "amount": price["amount_cents"],
                "currency": "INR",
                "prefill": {"name": user.name or "", "email": user.email},
            },
        }

    @classmethod
    def confirm_checkout(cls, user, payment_id, razorpay_subscription_id, signature):
        """Grant access for a checkout the browser says has just succeeded.

        Two independent checks, both required: Razorpay's signature proves
        the result wasn't forged, and a fresh read of the subscription proves
        it is paid *now* and belongs to this user.
        """
        if not cls.verify_checkout_signature(payment_id, razorpay_subscription_id, signature):
            logger.warning("Rejected a Razorpay checkout confirmation with a bad signature (user_id=%s).", user.id)
            raise PaymentVerificationError()

        entity = cls.fetch_subscription(razorpay_subscription_id)
        notes = entity.get("notes") or {}
        if str(notes.get("user_id")) != str(user.id):
            logger.warning(
                "Razorpay subscription %s confirmed by user_id=%s but belongs to user_id=%s.",
                razorpay_subscription_id, user.id, notes.get("user_id"),
            )
            raise PaymentVerificationError()
        if entity.get("status") not in ACTIVE_STATUSES:
            raise PaymentVerificationError()

        from billing.services.razorpay_sync_service import RazorpaySyncService

        with transaction.atomic():
            subscription = RazorpaySyncService.apply_subscription(
                entity, event_at=None, user_id=user.id
            )

        # The first invoice, straight away rather than whenever the
        # `subscription.charged` webhook lands. Best-effort: the webhook
        # issues it too, and issuing is idempotent on the payment id.
        try:
            payment = cls.fetch_payment(payment_id)
            if payment.get("status") == "captured":
                RazorpaySyncService.issue_invoice_for_payment(subscription, entity, payment)
        except Exception:
            logger.exception("Could not issue the first invoice for payment %s at confirmation.", payment_id)

        return subscription

    @classmethod
    def cancel_for_user(cls, user):
        """Customer-initiated cancellation: stop renewing, keep access until
        the end of the period already paid for."""
        subscription = Subscription.objects.filter(user=user).first()
        if (
            subscription is None
            or subscription.payment_processor != PROCESSOR_RAZORPAY
            or not subscription.razorpay_subscription_id
        ):
            raise BillingError("There's no rupee subscription on this account to cancel.")

        cls.cancel_subscription(subscription.razorpay_subscription_id, at_cycle_end=True)
        # Access is untouched here on purpose. Razorpay moves the
        # subscription to "cancelled" at the end of the cycle and says so by
        # webhook; until then the customer has what they paid for.
        subscription.cancel_at_period_end = True
        subscription.save(update_fields=["cancel_at_period_end", "updated_at"])
        return subscription


def unix_to_datetime(value):
    from datetime import datetime, timezone as dt_timezone

    if not value:
        return None
    return datetime.fromtimestamp(int(value), tz=dt_timezone.utc)


def now_unix():
    return int(time.time())
