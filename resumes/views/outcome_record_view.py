from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import serializers, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from resumes.models import JobResumeCuration
from resumes.services.outcome_nudge_service import resolve_outcome_token


class RecordOutcomeRequestSerializer(serializers.Serializer):
    token = serializers.CharField()
    application_status = serializers.ChoiceField(
        choices=[
            JobResumeCuration.APPLICATION_STATUS_APPLIED,
            JobResumeCuration.APPLICATION_STATUS_INTERVIEWING,
            JobResumeCuration.APPLICATION_STATUS_OFFER,
            JobResumeCuration.APPLICATION_STATUS_REJECTED,
        ]
    )


@extend_schema(
    tags=["Job Resume Curation"],
    summary="Record an application outcome from an emailed follow-up link",
    description=(
        "Sets the outcome of one application using the signed token from the "
        "'did you hear back?' email. Deliberately unauthenticated: requiring "
        "a login to answer a one-click question is exactly the friction that "
        "stops outcome data being collected at all.\n\n"
        "The token is signed, expires, and grants nothing beyond setting the "
        "status on the single application it was minted for - it is not a "
        "session and cannot read any user data. An invalid, tampered, or "
        "expired token returns the same 400 as an unknown one, so the "
        "endpoint cannot be used to probe which applications exist."
    ),
    request=RecordOutcomeRequestSerializer,
    responses={
        status.HTTP_200_OK: OpenApiResponse(description="Outcome recorded."),
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(description="Invalid or expired link."),
    },
)
class RecordOutcomeAPIView(APIView):
    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_scope = "outcome_record"

    def post(self, request):
        serializer = RecordOutcomeRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        curation = resolve_outcome_token(serializer.validated_data["token"])
        if curation is None:
            return Response(
                {"detail": "This link is invalid or has expired.", "code": "INVALID_OUTCOME_TOKEN"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        curation.application_status = serializer.validated_data["application_status"]
        curation.save(update_fields=["application_status", "updated_at"])

        return Response(
            {
                "detail": "Thanks — outcome recorded.",
                "job_title": curation.job_title,
                "application_status": curation.application_status,
            }
        )
