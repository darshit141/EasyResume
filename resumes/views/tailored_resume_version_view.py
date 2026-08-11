from drf_spectacular.utils import (
    OpenApiExample,
    OpenApiParameter,
    OpenApiResponse,
    extend_schema,
)
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from resumes.serializers.resume_regeneration_serializer import (
    TailoredResumeVersionDetailSerializer,
    TailoredResumeVersionListSerializer,
)
from resumes.services.tailored_resume_version_service import (
    TailoredResumeVersionService,
)
from resumes.views.job_curation_view import CURATION_ID_PARAMETER


VERSION_ID_PARAMETER = OpenApiParameter(
    name="version_id",
    type=int,
    location=OpenApiParameter.PATH,
    required=True,
    description="The tailored resume version ID, from tailored_resume_version.id in a generate/regenerate response, or from this list endpoint.",
)


@extend_schema(
    tags=["Job Resume Curation"],
    summary="Step 11 — Get tailored resume version history",
    description=(
        "**Workflow step 11 — job curation / tailored resume history.** "
        "Lists every tailored resume ever generated for this job curation, "
        "newest first, with each version's `id`, `version` number, "
        "`match_percentage` at the time it was generated, and "
        "`created_at`. Regenerating (see the tailored-resume endpoint) "
        "always creates a new version rather than deleting or overwriting "
        "a prior one, so this history is preserved across regenerations. "
        "Use this to render a \"previous versions\" picker. Call the "
        "detail endpoint below to fetch one version's full content. "
        "Requires a valid JWT bearer token; only versions belonging to "
        "the authenticated user's own curation are returned (an empty "
        "list, not an error, for a curation the user does not own — the "
        "curation's own endpoints return 404 instead, since curation "
        "ownership is checked there)."
    ),
    parameters=[CURATION_ID_PARAMETER],
    responses={
        status.HTTP_200_OK: TailoredResumeVersionListSerializer(many=True),
        status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
    },
    examples=[
        OpenApiExample(
            "Version history",
            value=[
                {
                    "id": 7,
                    "version": 2,
                    "match_percentage": "80.00",
                    "created_at": "2026-08-11T10:05:00Z",
                },
                {
                    "id": 4,
                    "version": 1,
                    "match_percentage": "60.00",
                    "created_at": "2026-08-11T10:00:05Z",
                },
            ],
            response_only=True,
            status_codes=["200"],
        ),
    ],
)
class TailoredResumeVersionListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, curation_id):
        versions = TailoredResumeVersionService.get_versions(
            request.user,
            curation_id,
        )
        return Response(
            TailoredResumeVersionListSerializer(versions, many=True).data,
            status=status.HTTP_200_OK,
        )


@extend_schema(
    tags=["Job Resume Curation"],
    summary="Get one tailored resume version's full content",
    description=(
        "Retrieve one specific tailored resume version, including its "
        "full `tailored_resume_data` and the `gap_analysis_snapshot` it "
        "was generated from. Requires a valid JWT bearer token. Only a "
        "version belonging to the authenticated user's own curation is "
        "visible (404 for any other user's version, or an unknown ID)."
    ),
    parameters=[CURATION_ID_PARAMETER, VERSION_ID_PARAMETER],
    responses={
        status.HTTP_200_OK: TailoredResumeVersionDetailSerializer,
        status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
        status.HTTP_404_NOT_FOUND: OpenApiResponse(
            description="No such version exists for this curation, owned by the authenticated user."
        ),
    },
)
class TailoredResumeVersionDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, curation_id, version_id):
        version = TailoredResumeVersionService.get_version(
            request.user,
            curation_id,
            version_id,
        )
        return Response(
            TailoredResumeVersionDetailSerializer(version).data,
            status=status.HTTP_200_OK,
        )
