from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.services.account_export_service import AccountExportService


@extend_schema(
    tags=["Account"],
    summary="Export all of the authenticated user's personal data",
    description=(
        "Returns a structured, complete export of everything this "
        "application holds about the authenticated user: profile, "
        "resumes and their version history, job curations (job analysis, "
        "gap analysis, tailored resume, cover letter) and their version "
        "history, subscription status, an AI-usage summary, and "
        "consent/policy-acceptance records. See GDPR_DATA_FLOW.md for the "
        "full inventory. Served as a downloadable JSON file."
    ),
    responses={
        status.HTTP_200_OK: OpenApiResponse(description="A downloadable JSON file of the user's data."),
        status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
    },
)
class AccountExportAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        export_data = AccountExportService.build_export(request.user)
        response = Response(export_data, content_type="application/json")
        response["Content-Disposition"] = 'attachment; filename="intelliresume-data-export.json"'
        return response
