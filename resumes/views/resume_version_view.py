from drf_spectacular.utils import extend_schema
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
    description="Retrieve all versions of the logged-in user's resume.",
    responses={
        status.HTTP_200_OK: ResumeVersionSerializer(many=True),
    },
)
class ResumeVersionAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        versions = ResumeVersionService.get_versions(
            request.user
        )

        serializer = ResumeVersionSerializer(
            versions,
            many=True,
        )

        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
        )