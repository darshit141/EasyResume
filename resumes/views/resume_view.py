from drf_spectacular.utils import extend_schema
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from resumes.serializers.resume_view_serializer import ResumeViewSerializer
from resumes.services.resume_view_service import ResumeViewService


@extend_schema(
    tags=["Resume"],
    summary="View Resume",
    description="Retrieve the logged-in user's uploaded resume.",
    responses=ResumeViewSerializer,
)
class ResumeViewAPIView(APIView):
    """
    API for retrieving the logged-in user's resume.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        resume = ResumeViewService.get_resume(request.user)

        serializer = ResumeViewSerializer(resume)

        return Response(serializer.data)