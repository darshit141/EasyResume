from django.conf import settings
from api.utils.config import get_config_value
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from resumes.serializers.resume_view_serializer import ResumeListItemSerializer
from resumes.services.resume_list_service import ResumeListService


@extend_schema(
    tags=["Resume"],
    summary="List all of the logged-in user's resumes",
    description=(
        "Returns every resume the user has uploaded, newest first — up to "
        "`settings.MAX_RESUMES_PER_USER` at a time (uploading past that "
        "limit fails until one is deleted). Excludes `parsed_data`; fetch "
        "`GET /api/resumes/<id>/` for one resume's full content."
    ),
    responses={status.HTTP_200_OK: ResumeListItemSerializer(many=True)},
)
class ResumeListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        resumes = ResumeListService.list_resumes(request.user)
        return Response(
            {
                "resumes": ResumeListItemSerializer(resumes, many=True).data,
                "limit": get_config_value("MAX_RESUMES_PER_USER", settings.MAX_RESUMES_PER_USER),
            },
            status=status.HTTP_200_OK,
        )
