import logging

from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiResponse,
    extend_schema,
)
from django.db import DatabaseError
from django.http import Http404, HttpResponse
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from resumes.models import JobResumeCuration
from resumes.serializers.job_curation_serializer import (
    APIErrorSerializer,
    ResumeCurationGenerateRequestSerializer,
)
from resumes.serializers.resume_regeneration_serializer import (
    JobResumeCurationRegenerationResponseSerializer,
)
from resumes.services.job_curation_service import JobCurationDatabaseError
from resumes.services.job_resume_curation_service import JobResumeCurationService
from resumes.services.llm_service import LLMServiceError, resolve_provider
from resumes.services.missing_information_review_service import (
    MissingInformationReviewService,
)
from resumes.services.tailored_resume_export_service import (
    TailoredResumeExportService,
)
from resumes.services.tailored_resume_version_comparison_service import (
    TailoredResumeVersionComparisonService,
)
from billing.models import AIUsageLog
from billing.services.entitlements_service import (
    EntitlementsService,
    PlanRestrictionError,
)
from billing.views.plan_limit_response import plan_restriction_response
from resumes.views.error_responses import llm_error_response
from resumes.views.job_curation_view import CURATION_ID_PARAMETER


logger = logging.getLogger(__name__)


TAILORED_RESUME_EXAMPLE = {
    "id": 1,
    "job_title": "Senior Java Backend Engineer",
    "status": "completed",
    "tailored_resume_version": {
        "id": 7,
        "version": 2,
        "created_at": "2026-08-11T10:00:05Z",
    },
    "tailored_resume_data": {
        "personal_information": {
            "name": "Jane Doe",
            "email": "jane@example.com",
            "phone": "1234567890",
        },
        "summary": (
            "Backend engineer with hands-on Java, Spring Boot, and Kafka "
            "experience building reliable, containerized services."
        ),
        "skills": ["Java", "Spring Boot", "Kafka", "Docker", "PostgreSQL"],
        "experience": [
            {
                "source_index": 0,
                "source_text": (
                    "Backend Engineer at Acme Corp (2019-2022) - built event-driven "
                    "services with Java, Spring Boot, and Kafka."
                ),
                "tailored_text": (
                    "Backend Engineer at Acme Corp (2019-2022) - designed and shipped "
                    "event-driven microservices with Java, Spring Boot, and Kafka."
                ),
            }
        ],
        "projects": [],
        "education": [],
        "certifications": [],
    },
    "tailored_resume_error_code": "",
    "gap_review": {
        "id": 1,
        "job_title": "Senior Java Backend Engineer",
        "overall_match_percentage": 80.0,
        "strong_matches": [
            {
                "requirement": "Java",
                "category": "required_skill",
                "match_status": "MATCHED",
                "reasoning": "Java is listed in verified candidate skills.",
            }
        ],
        "partial_matches": [],
        "missing_requirements": [
            {
                "requirement": "AWS",
                "category": "cloud_technology",
                "profile_section": "skills",
                "message": (
                    "AWS is listed as a job requirement but is not present "
                    "in your current profile."
                ),
                "guidance": (
                    "If you genuinely have AWS experience that is not "
                    "currently recorded, add it to your profile. Otherwise, "
                    "consider AWS a current skill gap."
                ),
            }
        ],
        "missing_candidate_information": [],
        "recommendations": [],
        "ats_keyword_gaps": {
            "covered_keywords": ["Java"],
            "missing_keywords": ["AWS"],
            "coverage_percentage": 80.0,
        },
        "suggested_profile_sections": ["skills"],
    },
    "version_comparison": {
        "previous_version": 1,
        "previous_match_percentage": "60.00",
        "current_match_percentage": "80.00",
        "match_percentage_delta": 20.0,
        "newly_matched_requirements": ["Docker"],
        "no_longer_matched_requirements": [],
        "newly_emphasized_skills": ["Docker"],
    },
    "created_at": "2026-08-11T10:00:00Z",
    "updated_at": "2026-08-11T10:00:05Z",
}


@extend_schema(
    tags=["Job Resume Curation"],
    summary="Steps 6-7 — Generate, get, or regenerate the tailored resume",
    description=(
        "**Workflow steps 6-7 — generate tailored resume / get generated "
        "resume / regenerate tailored resume.** This one endpoint covers "
        "all three: it is idempotent-by-default (call it with "
        "`regenerate: false` — the default — to either generate the first "
        "tailored resume or fetch the existing one from cache; \"get "
        "generated resume\" is simply calling this with `regenerate: "
        "false` after a version already exists). Pass `regenerate: true` "
        "to force the full pipeline to re-run against the latest data:\n\n"
        "1. Load the authenticated user's latest completed profile.\n"
        "2. Load this job curation session (structured job requirements).\n"
        "3. Re-run the candidate-vs-job comparison when regenerate=true, or "
        "reuse a cached comparison otherwise.\n"
        "4. Recalculate the match score and gap summary from that "
        "comparison.\n"
        "5. Generate the tailored resume using only the latest verified "
        "candidate information.\n"
        "6. Validate every generated skill, experience, project, education, "
        "and certification entry against the candidate's verified source "
        "data; unsupported content is rejected rather than silently "
        "accepted.\n"
        "7. Persist the result as a new, numbered tailored-resume version "
        "(`tailored_resume_version` in the response). Prior versions are "
        "never deleted or overwritten and remain available via the "
        "version history endpoints below.\n\n"
        "By default (regenerate=false) a previously generated tailored "
        "resume is returned from cache with no new version created. Pass "
        "regenerate=true after updating the profile (see "
        "`PUT /api/resumes/edit/` and the missing-information review "
        "endpoint) to force steps 3-7 to run again with the latest "
        "profile data. If generation fails for any reason (AI unavailable, "
        "malformed output, ungrounded content, database error), the "
        "previously persisted tailored resume and its version history are "
        "left completely untouched — regeneration never corrupts prior "
        "output. Internal prompts are not exposed.\n\n"
        "The response also bundles a freshly consistent `gap_review` "
        "(match score, strong/partial/missing requirements) computed from "
        "the same comparison used to generate the resume, so a single call "
        "is enough to update both the resume and the on-screen match "
        "summary.\n\n"
        "`version_comparison` is a factual, deterministically computed diff "
        "against the immediately prior version (match-percentage delta, "
        "newly matched requirements, newly emphasized skills) — never "
        "AI-generated commentary. It is `null` when this is the first "
        "version (nothing to compare against).\n\n"
        "Requires a valid JWT bearer token. Only the owning user's session "
        "is visible (404 otherwise) — a user may regenerate only their own "
        "job-specific resumes. **Synchronous:** on a cache miss or "
        "regenerate=true this blocks on one or two LLM calls (gap analysis "
        "then tailoring; up to one automatic retry on ungrounded content), "
        "bounded by `LLM_TIMEOUT` per call. There is no background job or "
        "polling; show a loading state and set the client timeout above "
        "roughly 2x `LLM_TIMEOUT`."
    ),
    parameters=[CURATION_ID_PARAMETER],
    request=ResumeCurationGenerateRequestSerializer,
    responses={
        status.HTTP_200_OK: JobResumeCurationRegenerationResponseSerializer,
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(
            description="`regenerate` was supplied but was not a valid boolean."
        ),
        status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
        status.HTTP_404_NOT_FOUND: OpenApiResponse(
            description="No curation with this ID exists for the authenticated user."
        ),
        status.HTTP_502_BAD_GATEWAY: OpenApiResponse(
            response=APIErrorSerializer,
            description=(
                "AI output could not be trusted. code: "
                "LLM_SCHEMA_VALIDATION_FAILED | LLM_INVALID_RESPONSE | "
                "RESUME_CURATION_GROUNDING_FAILED (the AI attempted to "
                "introduce a skill, employer, date, or metric not present "
                "in the candidate's verified profile, and retrying with a "
                "stricter prompt still failed)."
            ),
        ),
        status.HTTP_503_SERVICE_UNAVAILABLE: OpenApiResponse(
            response=APIErrorSerializer,
            description=(
                "AI unavailable, or prerequisite data missing. code: "
                "LLM_UNAVAILABLE | LLM_MODEL_UNAVAILABLE | LLM_TIMEOUT | "
                "CANDIDATE_DATA_UNAVAILABLE | JOB_REQUIREMENTS_UNAVAILABLE."
            ),
        ),
        status.HTTP_500_INTERNAL_SERVER_ERROR: OpenApiResponse(
            response=APIErrorSerializer,
            description="Database error while persisting the new version. code: DATABASE_ERROR.",
        ),
    },
    examples=[
        OpenApiExample(
            "Generate / get cached request",
            value={"regenerate": False},
            request_only=True,
        ),
        OpenApiExample(
            "Regenerate request",
            value={"regenerate": True},
            request_only=True,
        ),
        OpenApiExample(
            "Tailored resume response",
            value=TAILORED_RESUME_EXAMPLE,
            response_only=True,
            status_codes=["200"],
        ),
        OpenApiExample(
            "AI fabricated content was rejected",
            value={
                "detail": "Generated resume content could not be verified against candidate data.",
                "code": "RESUME_CURATION_GROUNDING_FAILED",
            },
            response_only=True,
            status_codes=["502"],
        ),
    ],
)
class JobResumeCurationGenerateAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "ai_resume"

    def post(self, request, curation_id):
        serializer = ResumeCurationGenerateRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        regenerate = serializer.validated_data["regenerate"]
        provider = serializer.validated_data.get("ai_provider")

        # Plan gates run before any AI call, so a user who is over quota gets
        # an instant, clear answer instead of waiting out a generation we
        # were never going to let them keep.
        curation = JobResumeCuration.objects.filter(id=curation_id, user=request.user).first()
        if curation is None:
            raise Http404("Job curation analysis not found.")

        # Returning an already-generated resume from cache creates no new
        # version and costs nothing, so it must not consume quota.
        creates_new_version = regenerate or not curation.tailored_resume_data

        try:
            EntitlementsService.check_ai_provider_allowed(request.user, provider)
            if creates_new_version:
                EntitlementsService.check_can_generate_tailored_resume(request.user)
                EntitlementsService.check_can_use_ai(request.user)
        except PlanRestrictionError as exc:
            return plan_restriction_response(exc)

        try:
            curation, version = JobResumeCurationService(provider=provider).generate(
                user=request.user,
                curation_id=curation_id,
                regenerate=regenerate,
            )
            gap_review = MissingInformationReviewService(provider=provider).build_review(
                user=request.user,
                curation_id=curation_id,
                regenerate=False,
            )
        except JobResumeCuration.DoesNotExist as exc:
            raise Http404("Job curation analysis not found.") from exc
        except LLMServiceError as exc:
            logger.warning("Job-tailored resume generation failed: %s", exc.code)
            return llm_error_response(exc)
        except JobCurationDatabaseError as exc:
            return Response(
                {"detail": exc.message, "code": exc.code},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        except DatabaseError:
            logger.exception("Unexpected database error during resume curation.")
            return Response(
                {
                    "detail": "Unable to save job-tailored resume data.",
                    "code": "DATABASE_ERROR",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        if creates_new_version:
            EntitlementsService.record_ai_usage(
                request.user, AIUsageLog.ACTION_TAILORED_RESUME_GENERATION, resolve_provider(provider)
            )

        version_comparison = TailoredResumeVersionComparisonService.compare_with_previous(
            curation, version
        )

        response_data = {
            "id": curation.id,
            "job_title": curation.job_title,
            "status": curation.status,
            "tailored_resume_version": version,
            "tailored_resume_data": curation.tailored_resume_data,
            "tailored_resume_error_code": curation.tailored_resume_error_code,
            "gap_review": gap_review,
            "version_comparison": version_comparison,
            "created_at": curation.created_at,
            "updated_at": curation.updated_at,
        }
        return Response(
            JobResumeCurationRegenerationResponseSerializer(response_data).data
        )


@extend_schema(
    tags=["Job Resume Curation"],
    summary="Export the current tailored resume as a .docx file",
    description=(
        "Downloads the most recently generated tailored resume for this "
        "curation as a real Word (.docx) document — no AI call, purely "
        "formats the already-validated, source-grounded "
        "`tailored_resume_data`. Requires a valid JWT bearer token. Only "
        "the owning user's curation is accessible (404 otherwise). "
        "Returns 404 if no tailored resume has been generated yet for "
        "this curation — generate one first via "
        "`POST .../tailored-resume/`."
    ),
    parameters=[CURATION_ID_PARAMETER],
    responses={
        status.HTTP_200_OK: OpenApiResponse(
            description="The .docx file, as a binary download (Content-Type: application/vnd.openxmlformats-officedocument.wordprocessingml.document)."
        ),
        status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
        status.HTTP_404_NOT_FOUND: OpenApiResponse(
            description="No curation with this ID exists for the authenticated user, or no tailored resume has been generated yet."
        ),
    },
)
class TailoredResumeDocxExportAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, curation_id):
        try:
            EntitlementsService.check_feature(request.user, "docx_export")
        except PlanRestrictionError as exc:
            return plan_restriction_response(exc)

        curation = JobResumeCuration.objects.filter(
            id=curation_id,
            user=request.user,
        ).first()
        if curation is None or not curation.tailored_resume_data:
            raise Http404("No tailored resume has been generated for this curation.")

        buffer = TailoredResumeExportService.build_docx(curation.tailored_resume_data)
        filename = TailoredResumeExportService.build_filename(curation.job_title)

        response = HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
