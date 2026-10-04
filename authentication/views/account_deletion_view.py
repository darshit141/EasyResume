from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.serializers.account_deletion_serializer import (
    AccountDeletionRequestSerializer,
)
from authentication.services import auth_security
from authentication.services.account_deletion_service import AccountDeletionService


@extend_schema(
    tags=["Account"],
    summary="Permanently delete the authenticated user's account",
    description=(
        "Irreversibly deletes the account and everything derived from it: "
        "resumes and their files, resume versions, job curations and "
        "tailored-resume/cover-letter versions, AI usage records, any "
        "active Stripe subscription (canceled immediately), coupon "
        "redemption history, and consent/policy-acceptance records. See "
        "GDPR_DATA_FLOW.md for the full inventory. Requires the current "
        "password — this cannot be undone, so a stolen/left-open session "
        "alone is not enough to trigger it."
    ),
    request=AccountDeletionRequestSerializer,
    responses={
        status.HTTP_200_OK: OpenApiResponse(description="Account permanently deleted."),
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(description="Incorrect password."),
        status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
    },
)
class AccountDeletionAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "auth_sensitive"

    def post(self, request):
        serializer = AccountDeletionRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        locked_for = auth_security.lockout_seconds_remaining(request.user)
        if locked_for:
            return Response(
                auth_security.locked_out_payload(locked_for),
                status=status.HTTP_429_TOO_MANY_REQUESTS,
            )

        if not request.user.check_password(serializer.validated_data["password"]):
            locked_for = auth_security.record_failed_login(request.user)
            if locked_for:
                return Response(
                    auth_security.locked_out_payload(locked_for),
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            return Response(
                {"password": ["Incorrect password."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        AccountDeletionService.delete_account(request.user)
        return Response({"message": "Account permanently deleted."})
