import json
import logging

from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.models import Subscription, StripeWebhookEvent
from billing.plans import (
    PLANS,
    PROCESSOR_RAZORPAY,
    SUPPORTED_CURRENCIES,
    processor_for,
    published_plans,
    format_price_display,
    get_price,
    available_currencies,
    customer_currency,
    default_currency,
    normalize_currency,
)
from billing.serializers.billing_serializer import (
    BillingErrorSerializer,
    CheckoutRequestSerializer,
    CheckoutResponseSerializer,
    CouponRedeemRequestSerializer,
    CouponRedeemResponseSerializer,
    PlanSerializer,
    PortalResponseSerializer,
    SubscriptionStatusSerializer,
)
from billing.serializers.billing_serializer import EntitlementsSerializer
from billing.services.coupon_service import CouponError
from billing.services.coupon_service import CouponService
from billing.services.entitlements_service import EntitlementsService
from billing.services.gst_service import GSTService
from billing.services.razorpay_service import RazorpayService
from billing.services.stripe_service import BillingError, StripeService
from billing.services.subscription_sync_service import SubscriptionSyncService

logger = logging.getLogger(__name__)


_UNAVAILABLE_CODES = {"STRIPE_NOT_CONFIGURED", "RAZORPAY_NOT_CONFIGURED", "RAZORPAY_ERROR"}

# How a customer can pay in each processor's checkout. Shown on the pricing
# page so an Indian visitor sees "UPI" before they commit to anything.
_PAYMENT_METHODS = {
    "stripe": ["card"],
    PROCESSOR_RAZORPAY: ["upi", "card", "netbanking"],
}


def _billing_error_response(exc):
    if exc.code in _UNAVAILABLE_CODES:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif exc.code == "ALREADY_SUBSCRIBED":
        status_code = status.HTTP_409_CONFLICT
    else:
        status_code = status.HTTP_400_BAD_REQUEST
    return Response({"detail": str(exc), "code": exc.code}, status=status_code)


def _coupon_error_response(exc):
    status_code = status.HTTP_404_NOT_FOUND if exc.code == "COUPON_NOT_FOUND" else status.HTTP_400_BAD_REQUEST
    return Response({"detail": str(exc), "code": exc.code}, status=status_code)


def _safe_event_payload(event):
    """Best-effort JSON-safe snapshot of a Stripe event for the audit log —
    never allowed to break webhook processing itself if it can't serialize
    cleanly."""
    try:
        return json.loads(json.dumps(dict(event), default=str))
    except Exception:
        return {"id": event.get("id"), "type": event.get("type")}


def _price_option(plan_id, currency, period, tax_note=""):
    price = get_price(plan_id, currency, period)
    if price is None:
        return None
    original = price.get("original_amount_cents")
    processor = price.get("processor", "stripe")
    return {
        "amount_cents": price["amount_cents"],
        "display": format_price_display(price["amount_cents"], currency, period),
        "purchasable": bool(price.get("processor_price_id")),
        "processor": processor,
        "payment_methods": _PAYMENT_METHODS.get(processor, ["card"]),
        "tax_inclusive": bool(price.get("tax_inclusive")),
        "tax_note": tax_note,
        # Present only while a promotion is running, so the card can show
        # the standard price struck through next to what is actually being
        # charged. Both numbers come from the same resolver as checkout.
        "original_amount_cents": original,
        "original_display": (
            format_price_display(original, currency, period) if original else None
        ),
        "offer": price.get("offer"),
    }


def _resolve_plan(plan, currency, tax_note=""):
    return {
        "id": plan["id"],
        "name": plan["name"],
        "features": plan["features"],
        "currency": currency,
        "monthly": _price_option(plan["id"], currency, "monthly", tax_note),
        "annual": _price_option(plan["id"], currency, "annual", tax_note),
    }


@extend_schema(
    tags=["Billing"],
    summary="List available plans",
    description=(
        "Returns the plan catalog (id, name, features, monthly/annual "
        "price) resolved to one currency. Public — no authentication "
        "required, so a pricing page can be shown to signed-out visitors. "
        "`purchasable` is false for any plan/currency/period combination "
        "whose Stripe Price ID hasn't been configured yet — the frontend "
        "should disable checkout for it. EUR and GBP prices exclude VAT, "
        "which Stripe Checkout calculates from the billing location. INR "
        "prices include GST and are paid through Razorpay (UPI, cards, "
        "netbanking); `tax_note` is the line to show under each price."
    ),
    parameters=[
        OpenApiParameter(
            name="currency",
            type=str,
            location=OpenApiParameter.QUERY,
            required=False,
            enum=list(SUPPORTED_CURRENCIES),
            description="EUR, GBP or INR. Defaults to EUR; an unsupported value also falls back to EUR.",
        )
    ],
    responses={status.HTTP_200_OK: PlanSerializer(many=True)},
)
class PlanListAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        currency = customer_currency(request.query_params.get("currency"))
        # Read once per request: the note depends on live GST settings, and
        # every price on the page should say the same thing.
        tax_note = GSTService.price_tax_note(currency)
        resolved = [_resolve_plan(plan, currency, tax_note) for plan in published_plans()]
        return Response(PlanSerializer(resolved, many=True).data)


@extend_schema(
    tags=["Billing"],
    summary="Which currencies can be paid in right now",
    description=(
        "Public. Lists the currencies checkout currently accepts and the one to show first. "
        "Stripe (EUR/GBP) is off for the India launch, so this is rupees only until it is "
        "switched back on in the admin Payments section."
    ),
    responses={status.HTTP_200_OK: OpenApiResponse(description="currencies, default_currency")},
)
class PaymentOptionsAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({"currencies": available_currencies(), "default_currency": default_currency()})


@extend_schema(
    tags=["Billing"],
    summary="Get the current user's subscription status",
    description="Returns the authenticated user's plan and subscription status. A user with no subscription history gets status \"none\".",
    responses={status.HTTP_200_OK: SubscriptionStatusSerializer},
)
class SubscriptionStatusAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        subscription, _ = Subscription.objects.get_or_create(user=request.user)
        return Response(SubscriptionStatusSerializer(subscription).data)


@extend_schema(
    tags=["Billing"],
    summary="Get the current user's plan entitlements and usage",
    description=(
        "What the authenticated user's plan currently allows, plus this "
        "month's usage. The frontend uses this to show quota counters and to "
        "hide or label gated controls — but every one of these limits is "
        "also enforced server-side, so this endpoint is a convenience, never "
        "the control itself. `tailored_resumes_limit` is null when unlimited."
    ),
    responses={status.HTTP_200_OK: EntitlementsSerializer},
)
class EntitlementsAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(EntitlementsService.usage_summary(request.user))


@extend_schema(
    tags=["Billing"],
    summary="Start checkout for a paid plan",
    description=(
        "Routes by currency. EUR/GBP: creates a Stripe Checkout session and "
        "returns its URL for the browser to redirect to. INR: creates a "
        "Razorpay subscription and returns what Razorpay Checkout needs to "
        "open (UPI AutoPay, cards, netbanking); the account's billing state "
        "must be saved first (code BILLING_STATE_REQUIRED otherwise), since "
        "it decides the GST place of supply. In neither case does this mark "
        "the subscription active — Stripe's webhook does, or for INR a "
        "signature-verified confirmation re-checked against Razorpay's API."
    ),
    request=CheckoutRequestSerializer,
    responses={
        status.HTTP_200_OK: CheckoutResponseSerializer,
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(response=BillingErrorSerializer),
        status.HTTP_503_SERVICE_UNAVAILABLE: OpenApiResponse(
            response=BillingErrorSerializer,
            description="Billing is not configured on this server yet. code: STRIPE_NOT_CONFIGURED.",
        ),
    },
)
class CreateCheckoutSessionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = CheckoutRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        currency = normalize_currency(serializer.validated_data.get("currency"))
        if currency not in available_currencies():
            return Response(
                {"detail": "That currency isn't available for payment yet.", "code": "CURRENCY_UNAVAILABLE"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            if processor_for(currency) == PROCESSOR_RAZORPAY:
                result = RazorpayService.start_checkout(
                    request.user,
                    serializer.validated_data["plan_id"],
                    billing_period=serializer.validated_data["billing_period"],
                )
            else:
                checkout_url = StripeService.create_checkout_session(
                    request.user,
                    serializer.validated_data["plan_id"],
                    currency=currency,
                    billing_period=serializer.validated_data["billing_period"],
                )
                result = {"processor": "stripe", "checkout_url": checkout_url}
        except BillingError as exc:
            return _billing_error_response(exc)

        return Response(CheckoutResponseSerializer(result).data)


@extend_schema(
    tags=["Billing"],
    summary="Create a Stripe Customer Portal session",
    description="Lets the user manage or cancel their own subscription via Stripe's hosted portal.",
    responses={
        status.HTTP_200_OK: PortalResponseSerializer,
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(response=BillingErrorSerializer),
        status.HTTP_503_SERVICE_UNAVAILABLE: OpenApiResponse(response=BillingErrorSerializer),
    },
)
class CreatePortalSessionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        subscription = Subscription.objects.filter(user=request.user).first()
        if subscription is not None and subscription.payment_processor == PROCESSOR_RAZORPAY:
            # Razorpay has no hosted customer portal. The settings page
            # offers cancellation and invoices directly for these accounts;
            # this says so if an older client asks anyway.
            return Response(
                {
                    "detail": "Rupee subscriptions are managed from Settings, not a billing portal.",
                    "code": "PORTAL_NOT_AVAILABLE",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            portal_url = StripeService.create_portal_session(request.user)
        except BillingError as exc:
            return _billing_error_response(exc)

        return Response(PortalResponseSerializer({"portal_url": portal_url}).data)


@extend_schema(
    tags=["Billing"],
    summary="Stripe webhook endpoint",
    description=(
        "Receives subscription lifecycle events from Stripe (checkout "
        "completed, subscription updated/canceled) and syncs local "
        "subscription state. Configure this URL in the Stripe Dashboard's "
        "webhook settings. Not for browser/frontend use."
    ),
    request=None,
    responses={
        status.HTTP_200_OK: OpenApiResponse(description="Event processed."),
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(description="Invalid signature or payload."),
    },
)
class StripeWebhookAPIView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        signature = request.META.get("HTTP_STRIPE_SIGNATURE", "")
        try:
            # construct_webhook_event returns a StripeObject in production
            # (it supports [] access but not .get(), which this view and
            # SubscriptionSyncService both use) — .to_dict() normalizes it
            # once, here at the boundary, so nothing downstream needs its
            # own StripeObject-vs-dict workaround. Tolerates a plain dict
            # too (already has no .to_dict) for tests that construct one
            # directly.
            raw_event = StripeService.construct_webhook_event(request.body, signature)
            event = raw_event.to_dict() if hasattr(raw_event, "to_dict") else raw_event
        except BillingError as exc:
            return _billing_error_response(exc)
        except Exception:
            logger.warning("Rejected Stripe webhook: invalid signature or payload.")
            return Response({"detail": "Invalid signature."}, status=status.HTTP_400_BAD_REQUEST)

        event_id = event.get("id") or ""
        event_type = event.get("type", "")

        webhook_event = None
        if event_id:
            webhook_event, created = StripeWebhookEvent.objects.get_or_create(
                stripe_event_id=event_id,
                defaults={"event_type": event_type, "payload": _safe_event_payload(event)},
            )
            if not created and webhook_event.processed_at is not None:
                # Stripe redelivers events (timeouts, manual replay). Without
                # this, a redelivered checkout.session.completed or
                # invoice.paid would reprocess business logic that already
                # ran once — a real correctness risk, not just noise.
                logger.info(
                    "Ignoring already-processed Stripe webhook event %s (%s)",
                    event_id, event_type,
                )
                return Response({"received": True, "duplicate": True})

        try:
            SubscriptionSyncService.apply_event(event)
        except Exception as exc:
            logger.exception("Failed to apply Stripe webhook event %s (%s)", event_id, event_type)
            if webhook_event is not None:
                webhook_event.error = str(exc)[:1000]
                webhook_event.save(update_fields=["error"])
            # 500, not 200: Stripe only retries on a non-2xx response, and a
            # genuine processing failure should be retried rather than
            # silently dropped.
            return Response({"detail": "Failed to process event."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if webhook_event is not None:
            webhook_event.processed_at = timezone.now()
            webhook_event.error = ""
            webhook_event.save(update_fields=["processed_at", "error"])

        return Response({"received": True})


@extend_schema(
    tags=["Billing"],
    summary="Redeem a coupon code for Pro access",
    description=(
        "Grants the authenticated user Pro access without Stripe — used for "
        "early access / beta invites and promo codes. Each code can be "
        "redeemed by a given user at most once, and stops working entirely "
        "once its configured redemption limit is reached (1 = single-use, "
        "the default). If the code grants a time-limited trial, access "
        "reverts to Free automatically once it expires — no action needed."
    ),
    request=CouponRedeemRequestSerializer,
    responses={
        status.HTTP_200_OK: CouponRedeemResponseSerializer,
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(
            response=BillingErrorSerializer,
            description="code: COUPON_EXHAUSTED | COUPON_ALREADY_REDEEMED.",
        ),
        status.HTTP_404_NOT_FOUND: OpenApiResponse(
            response=BillingErrorSerializer,
            description="No active coupon with that code. code: COUPON_NOT_FOUND.",
        ),
    },
)
class CouponRedeemAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "auth_sensitive"

    def post(self, request):
        serializer = CouponRedeemRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            subscription = CouponService.redeem(request.user, serializer.validated_data["code"])
        except CouponError as exc:
            return _coupon_error_response(exc)

        return Response(CouponRedeemResponseSerializer(subscription).data)
