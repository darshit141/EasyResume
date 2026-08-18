from django.http import Http404
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from resumes.serializers.resume_edit_serializer import (
    ResumeEditResponseSerializer,
    ResumeEditSerializer,
)
from resumes.serializers.resume_view_serializer import ResumeViewSerializer
from resumes.services.resume_delete_service import ResumeDeleteService
from resumes.services.resume_edit_service import ResumeEditService
from resumes.services.resume_view_service import ResumeViewService

RESUME_ID_PARAMETER = OpenApiParameter(
    name="resume_id",
    type=int,
    location=OpenApiParameter.PATH,
    required=True,
    description="One of the authenticated user's own resume ids, from GET /api/resumes/list/.",
)


@extend_schema(
    tags=["Resume"],
    summary="View, update, or delete one specific resume",
    description=(
        "Operates on exactly one resume the user owns, selected by id — "
        "the multi-resume counterpart to `/view/` and `/edit/`, which "
        "always act on the most recently uploaded resume instead. Only a "
        "resume belonging to the authenticated user is ever visible or "
        "modifiable (404 for any other id, including another user's)."
    ),
    parameters=[RESUME_ID_PARAMETER],
    responses={
        status.HTTP_404_NOT_FOUND: OpenApiResponse(description="No resume with that id owned by this user."),
    },
)
class ResumeDetailAPIView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(responses={status.HTTP_200_OK: ResumeViewSerializer})
    def get(self, request, resume_id):
        resume = ResumeViewService.get_resume_by_id(request.user, resume_id)
        return Response(ResumeViewSerializer(resume).data)

    @extend_schema(
        request=ResumeEditSerializer,
        responses={status.HTTP_200_OK: ResumeEditResponseSerializer},
    )
    def put(self, request, resume_id):
        serializer = ResumeEditSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        resume = ResumeEditService.update_resume_by_id(
            request.user, resume_id, serializer.validated_data["parsed_data"]
        )
        return Response(
            {
                "message": (
                    "Your profile has been updated. You can regenerate "
                    "your resume to reflect the new information."
                ),
                "ats_score": resume.ats_score,
            },
            status=status.HTTP_200_OK,
        )

    @extend_schema(responses={status.HTTP_204_NO_CONTENT: OpenApiResponse(description="Deleted.")})
    def delete(self, request, resume_id):
        ResumeDeleteService.delete_resume(request.user, resume_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
