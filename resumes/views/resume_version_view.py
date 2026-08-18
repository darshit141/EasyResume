from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from resumes.serializers.resume_version_serializer import (
    ResumeVersionSerializer,
)
from resumes.services.resume_version_service import (
    ResumeVersionService,
)


@extend_schema(
    tags=["Resume"],
    summary="Resume Version History",
    description=(
        "Retrieve all versions of one of the logged-in user's resumes. "
        "Pass `resume_id` to select which resume (from GET "
        "/api/resumes/list/); omit it for the most recently uploaded one."
    ),
    parameters=[
        OpenApiParameter(
            name="resume_id",
            type=int,
            location=OpenApiParameter.QUERY,
            required=False,
            description="Which resume's version history to fetch. Defaults to the most recently uploaded resume.",
        ),
    ],
    responses={
        status.HTTP_200_OK: ResumeVersionSerializer(many=True),
    },
)
class ResumeVersionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        resume_id = request.query_params.get("resume_id")
        versions = ResumeVersionService.get_versions(
            request.user, resume_id=resume_id
        )

        serializer = ResumeVersionSerializer(
            versions,
            many=True,
        )

        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
        )