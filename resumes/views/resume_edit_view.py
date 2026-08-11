from drf_spectacular.utils import OpenApiExample, OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from resumes.serializers.resume_edit_serializer import (
    ResumeEditResponseSerializer,
    ResumeEditSerializer,
)
from resumes.services.resume_edit_service import ResumeEditService


@extend_schema(
    tags=["Resume"],
    summary="Step 5 — Update candidate profile (existing profile API, reused)",
    description=(
        "**Workflow step 5 — update profile.** Replaces the authenticated "
        "user's most recent resume's parsed profile data and recalculates "
        "its ATS score. This is the single source of truth for the "
        "candidate's profile (skills, experience, projects, "
        "certifications, education, etc.) and is reused, unchanged, by "
        "the job-curation workflow — there is no separate, job-curation-"
        "specific profile-update endpoint or data model. After reviewing "
        "missing requirements for a job curation (see the "
        "missing-information review endpoint), update here any "
        "requirement the candidate genuinely already has but has not yet "
        "recorded, then call the job curation's tailored-resume endpoint "
        "with `regenerate: true` to refresh the tailored resume and gap "
        "analysis using the updated profile.\n\n"
        "**Important:** this replaces the *entire* `parsed_data` document "
        "— it is not a partial/patch update. Fetch the current profile "
        "with `GET /api/resumes/view/` first, modify it client-side, and "
        "send the full object back.\n\n"
        "A new resume version is created automatically for every "
        "successful edit (see `GET /api/resumes/versions/`); no prior "
        "profile version is ever deleted.\n\n"
        "Requires a valid JWT bearer token. Always operates on the "
        "authenticated user's own most recent resume — there is no ID "
        "parameter, so cross-user edits are structurally impossible."
    ),
    request=ResumeEditSerializer,
    responses={
        status.HTTP_200_OK: ResumeEditResponseSerializer,
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(
            description=(
                'Validation error: parsed_data missing. Response body is '
                'a field-name-keyed error map, e.g. {"parsed_data": '
                '["This field is required."]}.'
            )
        ),
        status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
        status.HTTP_404_NOT_FOUND: OpenApiResponse(
            description=(
                "The authenticated user has not uploaded a resume yet, so "
                "there is nothing to edit. Upload one first via "
                "POST /api/resumes/upload/."
            )
        ),
    },
    examples=[
        OpenApiExample(
            "Update profile request",
            value={
                "parsed_data": {
                    "personal_information": {
                        "name": "Jane Doe",
                        "email": "jane@example.com",
                        "phone": "1234567890",
                    },
                    "name": "Jane Doe",
                    "email": "jane@example.com",
                    "phone": "1234567890",
                    "summary": "Backend engineer with 5 years of experience.",
                    "skills": ["Java", "Spring Boot", "Kafka", "AWS"],
                    "experience": [
                        "Backend Engineer at Acme Corp (2019-2024) - built "
                        "event-driven services with Java, Spring Boot, and "
                        "Kafka."
                    ],
                    "education": ["B.Sc. Computer Science, State University, 2015-2019."],
                    "projects": [],
                    "certifications": ["AWS Certified Developer - Associate, 2023."],
                    "achievements": [],
                    "languages": ["English"],
                }
            },
            request_only=True,
        ),
        OpenApiExample(
            "Profile updated response",
            value={
                "message": (
                    "Your profile has been updated. You can regenerate your "
                    "resume to reflect the new information."
                ),
                "ats_score": 85.0,
            },
            response_only=True,
            status_codes=["200"],
        ),
    ],
)
class ResumeEditAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def put(self, request):
        serializer = ResumeEditSerializer(data=request.data)

        if serializer.is_valid():
            resume = ResumeEditService.update_resume(
                request.user,
                serializer.validated_data["parsed_data"],
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

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )
