import logging

from django.db import DatabaseError
from django.http import Http404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.models import AIUsageLog
from billing.services.entitlements_service import (
    EntitlementsService,
    PlanRestrictionError,
)
from billing.views.plan_limit_response import plan_restriction_response
from resumes.models import JobResumeCuration
from resumes.serializers.cover_letter_serializer import (
    ApplicationStatusSerializer,
    ApplicationStatusUpdateSerializer,
    CoverLetterGenerateRequestSerializer,
    CoverLetterResponseSerializer,
)
from resumes.serializers.job_curation_serializer import APIErrorSerializer
from resumes.services.cover_letter_service import CoverLetterService
from resumes.services.job_curation_service import JobCurationDatabaseError
from resumes.services.llm_service import LLMServiceError, resolve_provider
from resumes.views.error_responses import llm_error_response
from resumes.views.job_curation_view import CURATION_ID_PARAMETER

logger = logging.getLogger(__name__)


@extend_schema(
    tags=["Job Resume Curation"],
    summary="Generate, get, or regenerate the cover letter",
    description=(
        "Writes a cover letter for this curation using the same verified "
        "profile and match analysis the tailored resume is built from, so the "
        "two documents can never contradict each other.\n\n"
        "Every candidate claim is validated against the verified profile "
        "before it is returned — anything that can't be traced back is "
        "rejected and regenerated (`code: COVER_LETTER_GROUNDING_FAILED` if "
        "it still fails). Referring to the job posting's own wording is "
        "allowed; inventing candidate facts is not.\n\n"
        "`regenerate: false` (the default) returns a previously generated "
        "letter without spending an AI call. A failed regeneration never "
        "destroys the stored letter."
    ),
    parameters=[CURATION_ID_PARAMETER],
    request=CoverLetterGenerateRequestSerializer,
    responses={
        status.HTTP_200_OK: CoverLetterResponseSerializer,
        status.HTTP_402_PAYMENT_REQUIRED: OpenApiResponse(
            description="Plan limit reached. code: PLAN_LIMIT_REACHED | UPGRADE_REQUIRED."
        ),
        status.HTTP_404_NOT_FOUND: OpenApiResponse(description="No such curation for this user."),
        status.HTTP_409_CONFLICT: OpenApiResponse(
            response=APIErrorSerializer,
            description="The job analysis isn't complete yet. code: JOB_REQUIREMENTS_UNAVAILABLE.",
        ),
        status.HTTP_502_BAD_GATEWAY: OpenApiResponse(response=APIErrorSerializer),
        status.HTTP_503_SERVICE_UNAVAILABLE: OpenApiResponse(response=APIErrorSerializer),
    },
)
class CoverLetterGenerateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "ai_resume"

    def post(self, request, curation_id):
        serializer = CoverLetterGenerateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        regenerate = serializer.validated_data["regenerate"]
        provider = serializer.validated_data.get("ai_provider")

        curation = JobResumeCuration.objects.filter(id=curation_id, user=request.user).first()
        if curation is None:
            raise Http404("Job curation analysis not found.")

        # Returning a cached letter costs nothing, so it must not consume quota.
        will_call_ai = regenerate or not curation.cover_letter_data

        try:
            EntitlementsService.check_ai_provider_allowed(request.user, provider)
            if will_call_ai:
                EntitlementsService.check_can_use_ai(request.user)
        except PlanRestrictionError as exc:
            return plan_restriction_response(exc)

        try:
            curation = CoverLetterService(provider=provider).generate(
                user=request.user, curation_id=curation_id, regenerate=regenerate
            )
        except JobResumeCuration.DoesNotExist as exc:
            raise Http404("Job curation analysis not found.") from exc
        except LLMServiceError as exc:
            return llm_error_response(exc)
        except JobCurationDatabaseError as exc:
            return Response(
                {"detail": exc.message, "code": exc.code},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except DatabaseError:
            logger.exception("Unexpected database error during cover letter generation.")
            return Response(
                {"detail": "Unable to save cover letter data.", "code": "DATABASE_ERROR"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if will_call_ai:
            EntitlementsService.record_ai_usage(
                request.user, AIUsageLog.ACTION_COVER_LETTER, resolve_provider(provider)
            )

        return Response(CoverLetterResponseSerializer(curation).data)


@extend_schema(
    tags=["Job Resume Curation"],
    summary="Get or update this application's tracking status",
    description=(
        "The user's own record of where this application stands — status "
        "(saved / applied / interviewing / offer / rejected), the date they "
        "applied, and private notes. Purely user-managed: no AI call, no "
        "quota consumed, nothing here is ever set automatically."
    ),
    parameters=[CURATION_ID_PARAMETER],
    responses={
        status.HTTP_200_OK: ApplicationStatusSerializer,
        status.HTTP_404_NOT_FOUND: OpenApiResponse(description="No such curation for this user."),
    },
)
class ApplicationStatusAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def _get_curation(self, request, curation_id):
        curation = JobResumeCuration.objects.filter(id=curation_id, user=request.user).first()
        if curation is None:
            raise Http404("Job curation analysis not found.")
        return curation

    def get(self, request, curation_id):
        curation = self._get_curation(request, curation_id)
        return Response(ApplicationStatusSerializer(curation).data)

    @extend_schema(request=ApplicationStatusUpdateSerializer)
    def patch(self, request, curation_id):
        curation = self._get_curation(request, curation_id)
        serializer = ApplicationStatusUpdateSerializer(curation, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        serializer.save()
        return Response(ApplicationStatusSerializer(curation).data)
