from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from resumes.serializers.resume_edit_serializer import ResumeEditSerializer
from resumes.services.resume_edit_service import ResumeEditService


@extend_schema(
    tags=["Resume"],
    summary="Edit Resume",
    description="Update the parsed resume text and recalculate the ATS score.",
    request=ResumeEditSerializer,
    responses={
        status.HTTP_200_OK: ResumeEditSerializer,
    },
)
class ResumeEditAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request):
        serializer = ResumeEditSerializer(data=request.data)

        if serializer.is_valid():
            resume = ResumeEditService.update_resume(
                request.user,
                serializer.validated_data["parsed_text"],
            )

            return Response(
                {
                    "message": "Resume updated successfully.",
                    "ats_score": resume.ats_score,
                },
                status=status.HTTP_200_OK,
            )

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)