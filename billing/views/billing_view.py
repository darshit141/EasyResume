import logging

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.models import Subscription
from billing.plans import PLANS
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
from billing.services.stripe_service import BillingError, StripeService
from billing.services.subscription_sync_service import SubscriptionSyncService

logger = logging.getLogger(__name__)


def _billing_error_response(exc):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE if exc.code == "STRIPE_NOT_CONFIGURED" else status.HTTP_400_BAD_REQUEST
    return Response({"detail": str(exc), "code": exc.code}, status=status_code)


def _coupon_error_response(exc):
    status_code = status.HTTP_404_NOT_FOUND if exc.code == "COUPON_NOT_FOUND" else status.HTTP_400_BAD_REQUEST
    return Response({"detail": str(exc), "code": exc.code}, status=status_code)


@extend_schema(
    tags=["Billing"],
    summary="List available plans",
    description=(
        "Returns the plan catalog (id, name, price, features). Public — no "
        "authentication required, so a pricing page can be shown to signed-out "
        "visitors. `purchasable` is false for any plan whose Stripe Price ID "
        "hasn't been configured yet — the frontend should disable checkout for it."
    ),
    responses={status.HTTP_200_OK: PlanSerializer(many=True)},
)
class PlanListAPIView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        return Response(PlanSerializer(PLANS, many=True).data)


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
    summary="Create a Stripe Checkout session",
    description=(
        "Creates a Stripe subscription Checkout session for the requested plan "
        "and returns its URL — the frontend redirects the browser there. The "
        "local subscription is only marked active once Stripe confirms payment "
        "via webhook, never at checkout-session-creation time."
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

        try:
            checkout_url = StripeService.create_checkout_session(
                request.user, serializer.validated_data["plan_id"]
            )
        except BillingError as exc:
            return _billing_error_response(exc)

        return Response(CheckoutResponseSerializer({"checkout_url": checkout_url}).data)


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
            event = StripeService.construct_webhook_event(request.body, signature)
        except BillingError as exc:
            return _billing_error_response(exc)
        except Exception:
            logger.warning("Rejected Stripe webhook: invalid signature or payload.")
            return Response({"detail": "Invalid signature."}, status=status.HTTP_400_BAD_REQUEST)

        SubscriptionSyncService.apply_event(event)
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
