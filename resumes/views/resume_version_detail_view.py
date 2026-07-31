from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from resumes.serializers.resume_version_detail_serializer import (
    ResumeVersionDetailSerializer,
)
from resumes.services.resume_version_detail_service import (
    ResumeVersionDetailService,
)


@extend_schema(
    tags=["Resume"],
    summary="Resume Version Detail",
    description="Retrieve a specific version of the logged-in user's resume.",
    responses={
        status.HTTP_200_OK: ResumeVersionDetailSerializer,
    },
)
class ResumeVersionDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, version_id):
        version = ResumeVersionDetailService.get_version(
            request.user,
            version_id,
        )

        serializer = ResumeVersionDetailSerializer(
            version
        )

        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
        )