"""Rupee billing: Razorpay confirmation and webhooks, the buyer's billing
profile, and GST invoices."""

import json
import logging

from django.http import HttpResponse
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from billing import gst
from billing.models import BillingProfile, GSTInvoice, RazorpayWebhookEvent
from billing.serializers.billing_serializer import (
    BillingErrorSerializer,
    BillingProfileSerializer,
    GSTInfoSerializer,
    InvoiceSummarySerializer,
    RazorpayConfirmSerializer,
    SubscriptionStatusSerializer,
)
from billing.services.gst_invoice_service import GSTInvoiceService
from billing.services.gst_service import GSTService
from billing.services.razorpay_service import RazorpayService
from billing.services.razorpay_sync_service import RazorpaySyncService
from billing.services.stripe_service import BillingError

logger = logging.getLogger(__name__)


def _error(exc):
    from billing.views.billing_view import _billing_error_response

    return _billing_error_response(exc)


def _profile_payload(profile):
    state_code = profile.state_code if profile else ""
    return {
        "state_code": state_code,
        "state_name": gst.state_name(state_code),
        "gstin": profile.gstin if profile else "",
        "legal_name": profile.legal_name if profile else "",
        "address": profile.address if profile else "",
    }


@extend_schema(
    tags=["Billing"],
    summary="Your billing details for Indian GST invoices",
    description=(
        "The state you are billed in (the GST place of supply) and, "
        "optionally, a GSTIN with the registered business name and address. "
        "Needed before paying in rupees. A GSTIN changes the invoice so it "
        "can be used to claim input tax credit; it never changes the price."
    ),
)
class BillingProfileAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={status.HTTP_200_OK: BillingProfileSerializer})
    def get(self, request):
        profile = BillingProfile.objects.filter(user=request.user).first()
        return Response(_profile_payload(profile))

    @extend_schema(
        request=BillingProfileSerializer,
        responses={status.HTTP_200_OK: BillingProfileSerializer},
    )
    def put(self, request):
        serializer = BillingProfileSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        profile, _ = BillingProfile.objects.update_or_create(
            user=request.user,
            defaults={
                "country": "IN",
                "state_code": data["state_code"],
                "gstin": data["gstin"],
                "legal_name": data["legal_name"],
                "address": data["address"],
            },
        )
        return Response(_profile_payload(profile))


@extend_schema(
    tags=["Billing"],
    summary="Indian states for GST, and whether GST is being charged",
    responses={status.HTTP_200_OK: GSTInfoSerializer},
)
class GSTInfoAPIView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def get(self, request):
        supplier = GSTService.supplier()
        return Response({
            "registered": supplier.registered,
            "rate_percent": supplier.rate_percent if supplier.registered else "0",
            "states": [
                {"code": code, "name": name}
                for code, name in sorted(gst.STATES.items(), key=lambda item: item[1])
            ],
        })


@extend_schema(
    tags=["Billing"],
    summary="Confirm a Razorpay checkout",
    description=(
        "Called by the browser with the three values Razorpay Checkout hands "
        "back. The signature is verified with the API secret and the "
        "subscription is re-read from Razorpay before access is granted, so "
        "a forged or replayed result grants nothing. If this call never "
        "arrives — a closed tab, a dropped connection — the webhook applies "
        "the same payment a few moments later."
    ),
    request=RazorpayConfirmSerializer,
    responses={
        status.HTTP_200_OK: SubscriptionStatusSerializer,
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(response=BillingErrorSerializer),
    },
)
class RazorpayConfirmAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = RazorpayConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        try:
            subscription = RazorpayService.confirm_checkout(
                request.user,
                data["razorpay_payment_id"],
                data["razorpay_subscription_id"],
                data["razorpay_signature"],
            )
        except BillingError as exc:
            return _error(exc)
        if subscription is None:
            return _error(BillingError("That payment couldn't be applied to this account."))
        return Response(SubscriptionStatusSerializer(subscription).data)


@extend_schema(
    tags=["Billing"],
    summary="Cancel a rupee subscription",
    description=(
        "Stops renewal. Access continues until the end of the period already "
        "paid for. Stripe subscriptions are managed through the Stripe "
        "portal instead."
    ),
    request=None,
    responses={
        status.HTTP_200_OK: SubscriptionStatusSerializer,
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(response=BillingErrorSerializer),
    },
)
class RazorpayCancelAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        try:
            subscription = RazorpayService.cancel_for_user(request.user)
        except BillingError as exc:
            return _error(exc)
        return Response(SubscriptionStatusSerializer(subscription).data)


@extend_schema(
    tags=["Billing"],
    summary="Razorpay webhook endpoint",
    description=(
        "Receives subscription lifecycle and refund events from Razorpay. "
        "Configure it in the Razorpay Dashboard with the subscription.* "
        "events and refund.processed. Not for browser use."
    ),
    request=None,
    responses={
        status.HTTP_200_OK: OpenApiResponse(description="Event processed."),
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(description="Invalid signature or payload."),
    },
)
class RazorpayWebhookAPIView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []

    def post(self, request):
        signature = request.META.get("HTTP_X_RAZORPAY_SIGNATURE", "")
        # The signature covers the exact bytes Razorpay sent, so it is
        # checked against request.body before anything parses it.
        if not RazorpayService.verify_webhook_signature(request.body, signature):
            logger.warning("Rejected Razorpay webhook: invalid or missing signature.")
            return Response({"detail": "Invalid signature."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            event = json.loads(request.body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return Response({"detail": "Invalid payload."}, status=status.HTTP_400_BAD_REQUEST)

        event_id = request.META.get("HTTP_X_RAZORPAY_EVENT_ID", "")
        event_type = event.get("event", "")

        record = None
        if event_id:
            record, created = RazorpayWebhookEvent.objects.get_or_create(
                event_id=event_id,
                defaults={"event_type": event_type, "payload": event},
            )
            if not created and record.processed_at is not None:
                logger.info("Ignoring already-processed Razorpay event %s (%s).", event_id, event_type)
                return Response({"received": True, "duplicate": True})

        try:
            RazorpaySyncService.apply_event(event, event_id=event_id)
        except Exception as exc:
            logger.exception("Failed to apply Razorpay event %s (%s).", event_id, event_type)
            if record is not None:
                record.error = str(exc)[:1000]
                record.save(update_fields=["error"])
            # Non-2xx so Razorpay retries, as with Stripe.
            return Response({"detail": "Failed to process event."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        if record is not None:
            record.processed_at = timezone.now()
            record.error = ""
            record.save(update_fields=["processed_at", "error"])
        return Response({"received": True})


@extend_schema(
    tags=["Billing"],
    summary="Your invoices for rupee payments",
    responses={status.HTTP_200_OK: InvoiceSummarySerializer(many=True)},
)
class InvoiceListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        invoices = GSTInvoice.objects.filter(user=request.user).order_by("-issued_at")[:100]
        return Response([GSTInvoiceService.summary(invoice) for invoice in invoices])


@extend_schema(
    tags=["Billing"],
    summary="Download one invoice as PDF",
    responses={
        (status.HTTP_200_OK, "application/pdf"): OpenApiResponse(description="The invoice."),
        status.HTTP_404_NOT_FOUND: OpenApiResponse(description="No such invoice on this account."),
    },
)
class InvoicePdfAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, invoice_id):
        # Scoped to the requesting user: an invoice id belonging to someone
        # else is indistinguishable from one that doesn't exist.
        invoice = GSTInvoice.objects.filter(id=invoice_id, user=request.user).first()
        if invoice is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        pdf = GSTInvoiceService.render_pdf(invoice)
        response = HttpResponse(pdf, content_type="application/pdf")
        filename = f"IntelliResume-{invoice.number.replace('/', '-')}.pdf"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
