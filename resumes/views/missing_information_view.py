from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from django.http import Http404
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from resumes.models import JobResumeCuration
from resumes.serializers.job_curation_serializer import APIErrorSerializer
from resumes.serializers.missing_information_serializer import (
    MissingInformationQuerySerializer,
    MissingInformationReviewSerializer,
)
from resumes.services.llm_service import LLMServiceError
from resumes.services.missing_information_review_service import (
    MissingInformationReviewService,
)
from resumes.views.error_responses import llm_error_response
from resumes.views.job_curation_view import CURATION_ID_PARAMETER


MISSING_INFORMATION_EXAMPLE = {
    "id": 1,
    "job_title": "Senior Java Backend Engineer",
    "overall_match_percentage": 66.67,
    "strong_matches": [
        {
            "requirement": "Java",
            "category": "required_skill",
            "match_status": "MATCHED",
            "reasoning": "Java is listed in verified candidate skills.",
        }
    ],
    "partial_matches": [
        {
            "requirement": "5 years backend experience",
            "category": "experience_requirement",
            "match_status": "PARTIALLY_MATCHED",
            "reasoning": "Candidate data indicates 3 years against a 5-year requirement.",
        }
    ],
    "missing_requirements": [
        {
            "requirement": "AWS",
            "category": "cloud_technology",
            "profile_section": "skills",
            "message": (
                "AWS is listed as a job requirement but is not present in "
                "your current profile."
            ),
            "guidance": (
                "If you genuinely have AWS experience that is not currently "
                "recorded, add it to your profile. Otherwise, consider AWS a "
                "current skill gap."
            ),
        }
    ],
    "missing_candidate_information": [
        "Cloud deployment experience details are not present in the profile."
    ],
    "recommendations": [
        {
            "recommendation": "Add AWS only if you genuinely have AWS experience.",
            "recommendation_type": "MISSING_PROFILE_INFORMATION",
        }
    ],
    "ats_keyword_gaps": {
        "covered_keywords": ["Java", "Spring Boot"],
        "missing_keywords": ["AWS"],
        "coverage_percentage": 66.67,
    },
    "suggested_profile_sections": ["skills"],
}


@extend_schema(
    tags=["Job Resume Curation"],
    summary="Review missing profile information for a job curation",
    description=(
        "Turns the candidate-to-job gap analysis into an actionable review "
        "for the frontend: overall match, strong matches, partial matches, "
        "missing requirements (each mapped to a profile section to review), "
        "missing candidate information, recommendations, ATS keyword gaps, "
        "and a deduplicated list of profile sections worth revisiting.\n\n"
        "Every missing-requirement message is a fixed, backend-generated "
        "statement of fact (never AI free text) and never instructs the "
        "candidate to add a skill outright. It only states that the "
        "requirement is not present in the profile, and separately offers "
        "neutral guidance: add it if it is genuinely already possessed but "
        "unrecorded, otherwise treat it as a real skill gap.\n\n"
        "**Frontend workflow:**\n"
        "1. Call this endpoint (after `POST .../job-curations/analyze/` and "
        "the job curation reaching `completed` status) to display the "
        "review.\n"
        "2. For any missing requirement the candidate genuinely already has "
        "but simply has not recorded, update the profile using the "
        "existing `PUT /api/resumes/edit/` endpoint (no separate "
        "profile-update endpoint exists for this flow; the same resume "
        "data model is reused).\n"
        "3. After the profile is updated, call "
        "`POST .../job-curations/{curation_id}/tailored-resume/` with "
        "`regenerate: true` to refresh the tailored resume. That call also "
        "refreshes the underlying gap analysis, so the newly recorded "
        "information is reflected.\n"
        "4. Alternatively, call this endpoint again with `?regenerate=true` "
        "to refresh just the review without regenerating the tailored "
        "resume.\n\n"
        "Requires a valid JWT bearer token. Only the owning user's session "
        "is visible (404 otherwise). Synchronous: on a cache hit this "
        "returns instantly with no AI call; on a cache miss or "
        "`regenerate=true` it blocks on the LLM (same timeout/retry "
        "behavior as job analysis)."
    ),
    parameters=[
        CURATION_ID_PARAMETER,
        OpenApiParameter(
            name="regenerate",
            type=bool,
            location=OpenApiParameter.QUERY,
            required=False,
            description=(
                "When true, forces a fresh candidate-to-job comparison "
                "instead of reusing a cached one. Use after updating the "
                "profile."
            ),
        ),
    ],
    responses={
        status.HTTP_200_OK: MissingInformationReviewSerializer,
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(
            description="`regenerate` was supplied but was not a valid boolean."
        ),
        status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
        status.HTTP_404_NOT_FOUND: OpenApiResponse(
            description="No curation with this ID exists for the authenticated user."
        ),
        status.HTTP_502_BAD_GATEWAY: OpenApiResponse(
            response=APIErrorSerializer,
            description="AI response was malformed. code: LLM_SCHEMA_VALIDATION_FAILED | LLM_INVALID_RESPONSE.",
        ),
        status.HTTP_503_SERVICE_UNAVAILABLE: OpenApiResponse(
            response=APIErrorSerializer,
            description=(
                "AI unavailable, or no completed resume / structured job "
                "requirements exist yet. code: LLM_UNAVAILABLE | "
                "LLM_MODEL_UNAVAILABLE | LLM_TIMEOUT | "
                "CANDIDATE_DATA_UNAVAILABLE | JOB_REQUIREMENTS_UNAVAILABLE."
            ),
        ),
    },
    examples=[
        OpenApiExample(
            "Missing information review",
            value=MISSING_INFORMATION_EXAMPLE,
            response_only=True,
            status_codes=["200"],
        ),
        OpenApiExample(
            "AI service unavailable",
            value={
                "detail": "The configured AI model is unavailable.",
                "code": "LLM_MODEL_UNAVAILABLE",
            },
            response_only=True,
            status_codes=["503"],
        ),
    ],
)
class MissingInformationReviewAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, curation_id):
        query = MissingInformationQuerySerializer(data=request.query_params)
        if not query.is_valid():
            return Response(query.errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            review = MissingInformationReviewService().build_review(
                user=request.user,
                curation_id=curation_id,
                regenerate=query.validated_data["regenerate"],
            )
        except JobResumeCuration.DoesNotExist as exc:
            raise Http404("Job curation analysis not found.") from exc
        except LLMServiceError as exc:
            return llm_error_response(exc)

        return Response(MissingInformationReviewSerializer(review).data)
