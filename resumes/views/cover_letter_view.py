from django.http import Http404
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from ai_jobs.models import AIJob
from ai_jobs.serializers import AIJobSerializer
from ai_jobs.services import enqueue_ai_job
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
from resumes.services.job_gap_analysis_service import JobRequirementsUnavailableError
from resumes.services.llm_service import resolve_provider
from resumes.views.error_responses import llm_error_response
from resumes.views.job_curation_view import CURATION_ID_PARAMETER


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
        "letter without spending an AI call (200, synchronous). Otherwise "
        "this runs on a background worker: returns **202** with an AI job "
        "id — poll `GET /api/ai-jobs/{id}/` until `status` is `completed`; "
        "`result` then carries the same bundle the 200 response below "
        "documents. A failed regeneration never destroys the stored letter."
    ),
    parameters=[CURATION_ID_PARAMETER],
    request=CoverLetterGenerateRequestSerializer,
    responses={
        status.HTTP_200_OK: CoverLetterResponseSerializer,
        status.HTTP_202_ACCEPTED: OpenApiResponse(
            description="Queued for background generation. Poll GET /api/ai-jobs/{id}/ for the result."
        ),
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

        if not will_call_ai:
            # Cached path: no AI call, stays fully synchronous.
            return Response(CoverLetterResponseSerializer(curation).data)

        if curation.status != "completed" or not curation.analysis_data:
            return llm_error_response(JobRequirementsUnavailableError())

        # Runs on a background worker instead of blocking the request — see
        # ai_jobs.tasks._run_cover_letter_generation, which stores the same
        # response bundle this endpoint used to return directly.
        job = enqueue_ai_job(
            request.user,
            AIJob.JOB_TYPE_COVER_LETTER_GENERATION,
            provider=resolve_provider(provider),
            curation=curation,
            input_params={"regenerate": regenerate},
        )
        return Response(AIJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


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
