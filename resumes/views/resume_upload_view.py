from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_spectacular.utils import extend_schema

from resumes.serializers.resume_serializer import ResumeUploadSerializer
from resumes.services.resume_upload_service import ResumeUploadService

@extend_schema(
    request=ResumeUploadSerializer,
    responses={
        status.HTTP_201_CREATED: ResumeUploadSerializer,
    },
)
class ResumeUploadAPIView(APIView):
    """
    API to upload a resume.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        serializer = ResumeUploadSerializer(
            data=request.data,
            context={"request": request},
        )

        if serializer.is_valid():
            resume = ResumeUploadService.upload_resume(
                request.user,
                serializer.validated_data,
            )

            return Response(
                ResumeUploadSerializer(resume).data,
                status=status.HTTP_201_CREATED,
            )

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )