import hashlib
import hmac
import logging
import time
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Count, Q
from django.http import JsonResponse
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from inbox.models import InboundEmail
from inbox.serializers import AssignSerializer, InboundEmailDetailSerializer, InboundEmailSerializer
from inbox.services import status_updates
from inbox.services.addresses import email_for, receiving_live, rotate_tracking_address, tracking_address_for
from inbox.services.processing import (
    DailyLimitReached,
    UnknownRecipient,
    job_limit,
    receive,
    tracking_allowed,
)
from resumes.models import JobResumeCuration

logger = logging.getLogger(__name__)

_MAX_CLOCK_SKEW_SECONDS = 300


def _valid_signature(request, body):
    secret = settings.INBOUND_EMAIL_SECRET
    timestamp = request.headers.get("X-Inbound-Timestamp", "")
    signature = request.headers.get("X-Inbound-Signature", "")
    if not (secret and timestamp.isdigit() and signature):
        return False
    if abs(time.time() - int(timestamp)) > _MAX_CLOCK_SKEW_SECONDS:
        return False
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@method_decorator(csrf_exempt, name="dispatch")
class InboundEmailWebhookView(View):
    """Called by the Cloudflare Email Worker once per delivered message.

    The status code is the Worker's answer to the sending mail server: 404
    bounces mail to an address we don't recognise, 429 and 413 refuse it,
    and a 5xx makes the Worker fail the delivery temporarily so the sender
    retries later — nothing is lost while the API is down.
    """

    def post(self, request):
        if not settings.INBOUND_EMAIL_SECRET:
            return JsonResponse({"detail": "Inbound email is not configured."}, status=503)

        body = request.body
        if len(body) > settings.INBOUND_EMAIL_MAX_BYTES:
            return JsonResponse({"detail": "Message too large."}, status=413)
        if not _valid_signature(request, body):
            logger.warning("Rejected an inbound email with a bad or missing signature.")
            return JsonResponse({"detail": "Invalid signature."}, status=401)

        envelope_to = request.headers.get("X-Inbound-To", "")
        try:
            inbound, created = receive(body, envelope_to)
        except UnknownRecipient:
            return JsonResponse({"detail": "Unknown recipient."}, status=404)
        except DailyLimitReached:
            logger.warning("Inbound email daily limit reached for %s", envelope_to)
            return JsonResponse({"detail": "Too many messages for this address today."}, status=429)

        if created:
            from inbox.tasks import process_inbound_email_task

            inbound_id = inbound.id
            transaction.on_commit(lambda: _enqueue(process_inbound_email_task, inbound_id))
            return JsonResponse({"status": "accepted", "id": inbound_id}, status=202)
        return JsonResponse({"status": "duplicate", "id": inbound.id}, status=200)


def _enqueue(task, inbound_id):
    try:
        task.delay(inbound_id)
    except Exception:  # noqa: BLE001 - the periodic sweep picks it up
        logger.exception("Could not queue inbound email %s; the sweep will retry it.", inbound_id)


def _tracked_job_count(user):
    return (
        InboundEmail.objects.filter(user=user, curation__isnull=False)
        .values("curation_id").distinct().count()
    )


class TrackingAddressAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(tags=["Recruiter emails"], summary="The user's forwarding address and setup state")
    def get(self, request):
        address = tracking_address_for(request.user)
        return Response({
            "address": email_for("track", address.token),
            "enabled": tracking_allowed(request.user) and receiving_live(),
            "receiving_live": receiving_live(),
            "job_limit": job_limit(request.user),
            "tracked_jobs": _tracked_job_count(request.user),
            "forwarding_confirmation_code": address.forwarding_confirmation_code,
            "forwarding_confirmation_link": address.forwarding_confirmation_link,
            "forwarding_confirmation_received_at": address.forwarding_confirmation_received_at,
            "first_email_received_at": address.first_email_received_at,
        })


class RotateTrackingAddressAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(tags=["Recruiter emails"], summary="Replace the forwarding address with a new one")
    def post(self, request):
        address = rotate_tracking_address(request.user)
        return Response({"address": email_for("track", address.token)})


def _visible(user):
    return (
        InboundEmail.objects.filter(user=user, is_forwarding_confirmation=False)
        .exclude(category=InboundEmail.CATEGORY_NOT_JOB, curation__isnull=True)
        .select_related("curation", "locked_curation")
    )


class InboundEmailListAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(tags=["Recruiter emails"], summary="Recruiter emails, for one job or unmatched")
    def get(self, request):
        emails = _visible(request.user)
        curation_id = request.query_params.get("curation")
        if curation_id:
            emails = emails.filter(curation_id=curation_id)
        elif request.query_params.get("unmatched") in ("1", "true"):
            emails = emails.filter(curation__isnull=True, state=InboundEmail.STATE_PROCESSED)
        return Response(InboundEmailSerializer(emails[:100], many=True).data)


class InboundEmailDetailAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _get(self, request, email_id):
        return _visible(request.user).filter(pk=email_id).first()

    @extend_schema(tags=["Recruiter emails"], summary="One email, with its full text")
    def get(self, request, email_id):
        inbound = self._get(request, email_id)
        if inbound is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(InboundEmailDetailSerializer(inbound).data)

    @extend_schema(tags=["Recruiter emails"], summary="Delete a stored email")
    def delete(self, request, email_id):
        inbound = self._get(request, email_id)
        if inbound is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        inbound.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AssignInboundEmailAPIView(APIView):
    """The user saying which job an email belongs to (or none)."""

    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(tags=["Recruiter emails"], summary="File an email under a job", request=AssignSerializer)
    def post(self, request, email_id):
        inbound = _visible(request.user).filter(pk=email_id).first()
        if inbound is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        serializer = AssignSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        curation_id = serializer.validated_data["curation_id"]

        if curation_id is None:
            # "Not about any of my jobs": filed away, out of the to-sort list.
            inbound.curation, inbound.locked_curation, inbound.match_method = None, None, ""
            inbound.category = InboundEmail.CATEGORY_NOT_JOB
            inbound.suggested_status = ""
            inbound.save()
            return Response(InboundEmailSerializer(inbound).data)

        curation = JobResumeCuration.objects.filter(pk=curation_id, user=request.user).first()
        if curation is None:
            return Response({"curation_id": ["No such job."]}, status=status.HTTP_400_BAD_REQUEST)

        limit = job_limit(request.user)
        already_tracked = InboundEmail.objects.filter(user=request.user, curation=curation).exists()
        if limit is not None and not already_tracked and _tracked_job_count(request.user) >= limit:
            return Response(
                {
                    "detail": f"The Free plan tracks recruiter emails for {limit} jobs. Upgrade to track more.",
                    "code": "REPLY_TRACKING_JOB_LIMIT",
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        inbound.curation, inbound.locked_curation, inbound.match_method = curation, None, "manual"
        inbound.status_applied_at = None
        inbound.status_undone_at = None
        # The user filed it, but didn't ask for a status change: suggest only.
        inbound.suggested_status = status_updates.target_status(inbound.category, curation.application_status)
        inbound.save()
        return Response(InboundEmailSerializer(inbound).data)


class ApplyStatusAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(tags=["Recruiter emails"], summary="Accept an email's suggested status")
    def post(self, request, email_id):
        inbound = _visible(request.user).filter(pk=email_id, curation__isnull=False).first()
        if inbound is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if not status_updates.apply_suggestion(inbound):
            return Response(
                {"detail": "This email doesn't change the job's status any more."},
                status=status.HTTP_409_CONFLICT,
            )
        return Response(InboundEmailSerializer(inbound).data)


class UndoStatusAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(tags=["Recruiter emails"], summary="Undo a status change an email made")
    def post(self, request, email_id):
        inbound = _visible(request.user).filter(pk=email_id).first()
        if inbound is None:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        if not status_updates.undo(inbound):
            return Response(
                {"detail": "The job's status has changed since, so there's nothing to undo."},
                status=status.HTTP_409_CONFLICT,
            )
        inbound.refresh_from_db()
        return Response(InboundEmailSerializer(inbound).data)


class AdminInboundEmailStatsAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    @extend_schema(tags=["Admin"], summary="Recruiter email tracking health")
    def get(self, request):
        since = timezone.now() - timedelta(days=30)
        rows = InboundEmail.objects.filter(received_at__gte=since, is_forwarding_confirmation=False)
        totals = rows.aggregate(
            received=Count("id"),
            processed=Count("id", filter=Q(state=InboundEmail.STATE_PROCESSED)),
            failed=Count("id", filter=Q(state=InboundEmail.STATE_FAILED)),
            pending=Count("id", filter=Q(state=InboundEmail.STATE_PENDING)),
            matched=Count("id", filter=Q(curation__isnull=False)),
            auto_updated=Count("id", filter=Q(status_applied_automatically=True)),
            undone=Count("id", filter=Q(status_undone_at__isnull=False)),
            classified_by_ai=Count("id", filter=Q(classification_source="ai")),
        )
        by_category = dict(rows.values_list("category").annotate(count=Count("id")))
        return Response({
            "days": 30,
            "receiving_live": receiving_live(),
            "totals": totals,
            "by_category": by_category,
        })
