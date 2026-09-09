from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from resumes.models import Resume
from resumes.services.autofill_profile_service import AutofillProfileService


@extend_schema(
    tags=["Resume"],
    summary="The user's details as atomic application-form fields",
    description=(
        "Reshapes the user's most recent completed resume into the "
        "one-value-per-field form an ATS application asks for — first "
        "name, last name, email, phone, links, current role and employer, "
        "most recent qualification. Used by the browser extension to fill "
        "an application form.\n\n"
        "Any field that cannot be sourced from the resume comes back as an "
        "empty string, never inferred. The extension leaves empty fields "
        "alone, so an unknown value costs the user a moment's typing "
        "rather than putting a guess on an application under their name.\n\n"
        "`has_resume` is false when the account has no parsed resume yet; "
        "identity fields still come from the account itself."
    ),
    responses={
        status.HTTP_200_OK: OpenApiResponse(
            description=(
                "{full_name, first_name, last_name, email, phone, location, "
                "linkedin_url, github_url, website_url, current_title, "
                "current_company, degree, institution, summary, skills[], "
                "has_resume}"
            )
        )
    },
)
class AutofillProfileAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        resume = (
            Resume.objects.filter(user=request.user, status="completed")
            .exclude(parsed_data={})
            .order_by("-created_at")
            .first()
        )
        return Response(AutofillProfileService.build(request.user, resume))
