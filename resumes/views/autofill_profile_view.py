from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from resumes.models import Resume
from resumes.services.autofill_profile_service import AutofillProfileService
from resumes.services.autofill_resume_file_service import AutofillResumeFileService


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
    throttle_scope = "autofill"

    def get(self, request):
        resume = (
            Resume.objects.filter(user=request.user, status="completed")
            .exclude(parsed_data={})
            .order_by("-created_at")
            .first()
        )
        return Response(AutofillProfileService.build(request.user, resume))


@extend_schema(
    tags=["Resume"],
    summary="The user's resume file, for the extension to attach",
    description=(
        "Returns the user's most recent uploaded resume file, base64-encoded "
        "inside JSON, so the browser extension can attach it to an "
        "application form's file input.\n\n"
        "Base64 rather than a file download because of the two hops it has "
        "to survive: only the extension's service worker holds the auth "
        "token, and the only way from there into the page — where the file "
        "input lives — takes JSON-serialisable arguments, which a Blob is "
        "not.\n\n"
        "The extension calls this only when the user has ticked the attach "
        "box, and it attaches only to a file input that is empty.\n\n"
        "Always 200. `available: false` with a `reason` (`no_file`, "
        "`too_large`, `missing_from_storage`) is an ordinary answer, not an "
        "error — a resume built by the guided builder has no source file at "
        "all, and the user attaching it themselves is the intended fallback."
    ),
    responses={
        status.HTTP_200_OK: OpenApiResponse(
            description="{available, filename, content_type, size, data} or {available: false, reason}"
        )
    },
)
class AutofillResumeFileAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "autofill"

    def get(self, request):
        # Deliberately a different query from the profile above: that one
        # wants the most recent *parsed* resume, this one the most recent
        # resume that has a file at all. A guided-builder resume is the
        # newest and has no file, and returning nothing in that case would
        # hide an uploaded PDF the user still has.
        resume = (
            Resume.objects.filter(user=request.user)
            .exclude(file="")
            .exclude(file__isnull=True)
            .order_by("-created_at")
            .first()
        )
        return Response(AutofillResumeFileService.build(resume))
