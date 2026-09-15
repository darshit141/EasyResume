from django.http import Http404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.services.entitlements_service import (
    EntitlementsService,
    PlanRestrictionError,
)
from billing.views.plan_limit_response import plan_restriction_response
from resumes.models import JobApplication, JobResumeCuration
from resumes.serializers.job_application_serializer import (
    JobApplicationDraftRequestSerializer,
    JobApplicationSerializer,
    JobApplicationUpdateSerializer,
)
from resumes.serializers.job_curation_serializer import APIErrorSerializer
from resumes.services.job_application_service import (
    ApplicationDeliveryError,
    ApplicationNotSendableError,
    JobApplicationService,
)
from resumes.services.llm_service import LLMServiceError
from resumes.views.error_responses import llm_error_response
from resumes.views.job_curation_view import CURATION_ID_PARAMETER
from resumes.services.llm_service import requested_provider


def _not_sendable_response(exc):
    return Response(
        {"detail": exc.message, "code": exc.code},
        status=status.HTTP_409_CONFLICT,
    )


@extend_schema(
    tags=["Direct Apply"],
    summary="Draft an application email for this curation",
    description=(
        "Writes the short covering email the candidate will send with their "
        "application, grounded in their verified resume exactly the way the "
        "tailored resume and cover letter are — a claim that can't be traced "
        "back to their own data is rejected (`code: "
        "APPLICATION_EMAIL_GROUNDING_FAILED`).\n\n"
        "**This sends nothing.** It creates a draft the user reviews and "
        "edits; delivery happens only when they call the send endpoint.\n\n"
        "Synchronous, unlike cover letter generation: this is a 150-word "
        "email, not a full document, so queueing it would cost more in "
        "polling than it saves in request time."
    ),
    parameters=[CURATION_ID_PARAMETER],
    request=JobApplicationDraftRequestSerializer,
    responses={
        status.HTTP_201_CREATED: JobApplicationSerializer,
        status.HTTP_402_PAYMENT_REQUIRED: OpenApiResponse(
            description="Plan limit reached. code: PLAN_LIMIT_REACHED | UPGRADE_REQUIRED."
        ),
        status.HTTP_404_NOT_FOUND: OpenApiResponse(description="No such curation for this user."),
        status.HTTP_409_CONFLICT: OpenApiResponse(
            response=APIErrorSerializer,
            description="No tailored resume exists yet. code: TAILORED_RESUME_REQUIRED.",
        ),
        status.HTTP_502_BAD_GATEWAY: OpenApiResponse(response=APIErrorSerializer),
    },
)
class JobApplicationDraftAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "ai_resume"

    def post(self, request, curation_id):
        serializer = JobApplicationDraftRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        provider = requested_provider(data.get("provider") or None)

        if not JobResumeCuration.objects.filter(id=curation_id, user=request.user).exists():
            raise Http404("Job curation analysis not found.")

        try:
            EntitlementsService.check_ai_provider_allowed(request.user, provider)
            EntitlementsService.check_can_use_ai(request.user)
            # Checked at draft time as well as send time so a user who has
            # exhausted their allowance is told before they write an email
            # they can't send, not after.
            EntitlementsService.check_can_send_application(request.user)
        except PlanRestrictionError as exc:
            return plan_restriction_response(exc)

        try:
            application = JobApplicationService(provider=provider).create_draft(
                user=request.user,
                curation_id=curation_id,
                recipient_email=data["recipient_email"],
                recipient_name=data.get("recipient_name", ""),
                company_name=data.get("company_name", ""),
                include_resume=data.get("include_resume", True),
                include_cover_letter=data.get("include_cover_letter", False),
            )
        except ApplicationNotSendableError as exc:
            return _not_sendable_response(exc)
        except LLMServiceError as exc:
            return llm_error_response(exc)

        return Response(
            JobApplicationSerializer(application).data, status=status.HTTP_201_CREATED
        )


@extend_schema(
    tags=["Direct Apply"],
    summary="List this user's applications",
    description=(
        "Every application this user has drafted or sent, newest first — the "
        "review queue and the sent history are the same list, separated by "
        "`status`."
    ),
    responses={status.HTTP_200_OK: JobApplicationSerializer(many=True)},
)
class JobApplicationListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        applications = JobApplication.objects.filter(
            user=request.user
        ).select_related("curation")
        return Response(JobApplicationSerializer(applications, many=True).data)


@extend_schema(
    tags=["Direct Apply"],
    summary="Review, edit, or discard one application",
    description=(
        "`PATCH` accepts the user's edits to the message, the recipient and "
        "the attachment choices — but only while the application is still a "
        "draft. A sent application is an immutable record of what actually "
        "went out; letting it be rewritten afterwards would make the history "
        "a fiction.\n\n"
        "`DELETE` discards a draft. Sent applications cannot be deleted, for "
        "the same reason."
    ),
    responses={
        status.HTTP_200_OK: JobApplicationSerializer,
        status.HTTP_404_NOT_FOUND: OpenApiResponse(description="No such application for this user."),
        status.HTTP_409_CONFLICT: OpenApiResponse(
            response=APIErrorSerializer,
            description="The application has already been sent. code: APPLICATION_ALREADY_SENT.",
        ),
    },
)
class JobApplicationDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def _get(self, request, application_id):
        application = JobApplication.objects.filter(
            id=application_id, user=request.user
        ).select_related("curation").first()
        if application is None:
            raise Http404("Application not found.")
        return application

    def get(self, request, application_id):
        return Response(JobApplicationSerializer(self._get(request, application_id)).data)

    @extend_schema(request=JobApplicationUpdateSerializer)
    def patch(self, request, application_id):
        application = self._get(request, application_id)
        if application.status == JobApplication.STATUS_SENT:
            return Response(
                {
                    "detail": "This application has already been sent and can no longer be edited.",
                    "code": "APPLICATION_ALREADY_SENT",
                },
                status=status.HTTP_409_CONFLICT,
            )

        serializer = JobApplicationUpdateSerializer(application, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        serializer.save()
        return Response(JobApplicationSerializer(application).data)

    def delete(self, request, application_id):
        application = self._get(request, application_id)
        if application.status == JobApplication.STATUS_SENT:
            return Response(
                {
                    "detail": "A sent application is a permanent record and cannot be deleted.",
                    "code": "APPLICATION_ALREADY_SENT",
                },
                status=status.HTTP_409_CONFLICT,
            )
        application.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


@extend_schema(
    tags=["Direct Apply"],
    summary="Send a reviewed application",
    description=(
        "Delivers the application to the recipient, with the tailored resume "
        "attached as a PDF and `Reply-To` set to the candidate's own address "
        "so the employer replies to them, not to us.\n\n"
        "This is the only endpoint in the product that sends user-authored "
        "content to a third party, and it is deliberately a separate, "
        "explicit call — nothing is ever sent as a side effect of drafting. "
        "Re-sending an already-sent application is a no-op rather than an "
        "error, so a double-clicked button can't mail an employer twice.\n\n"
        "On success the linked job also moves to **applied** in the tracker, "
        "unless the user had already moved it somewhere else themselves."
    ),
    request=None,
    responses={
        status.HTTP_200_OK: JobApplicationSerializer,
        status.HTTP_402_PAYMENT_REQUIRED: OpenApiResponse(
            description="Monthly send allowance exhausted. code: PLAN_LIMIT_REACHED."
        ),
        status.HTTP_404_NOT_FOUND: OpenApiResponse(description="No such application for this user."),
        status.HTTP_409_CONFLICT: OpenApiResponse(
            response=APIErrorSerializer,
            description="Nothing to attach. code: TAILORED_RESUME_REQUIRED.",
        ),
        status.HTTP_502_BAD_GATEWAY: OpenApiResponse(
            response=APIErrorSerializer,
            description="The mail provider refused the message. code: APPLICATION_DELIVERY_FAILED.",
        ),
    },
)
class JobApplicationSendAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "ai_resume"

    def post(self, request, application_id):
        if not JobApplication.objects.filter(id=application_id, user=request.user).exists():
            raise Http404("Application not found.")

        try:
            application = JobApplicationService.send(request.user, application_id)
        except PlanRestrictionError as exc:
            return plan_restriction_response(exc)
        except ApplicationNotSendableError as exc:
            return _not_sendable_response(exc)
        except ApplicationDeliveryError as exc:
            return Response(
                {"detail": exc.message, "code": exc.code},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(JobApplicationSerializer(application).data)
