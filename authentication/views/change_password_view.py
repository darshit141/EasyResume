from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.serializers.change_password_serializer import (
    ChangePasswordSerializer,
)
from authentication.services.change_password_service import (
    ChangePasswordService,
)


@extend_schema(
    tags=["Account"],
    summary="Change password (authenticated)",
    description=(
        "Changes the authenticated user's password. Requires the current "
        "password. Distinct from the forgot-password/OTP flow, which is "
        "for users who cannot log in at all."
    ),
    request=ChangePasswordSerializer,
    responses={
        status.HTTP_200_OK: OpenApiResponse(description="Password changed successfully."),
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(
            description="Validation error, or current password is incorrect."
        ),
        status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
        status.HTTP_429_TOO_MANY_REQUESTS: OpenApiResponse(
            description="Too many wrong passwords; account temporarily locked."
        ),
    },
)
class ChangePasswordAPIView(APIView):
    permission_classes = [IsAuthenticated]
    throttle_scope = "auth_sensitive"

    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        result = ChangePasswordService.change_password(
            user=request.user,
            current_password=serializer.validated_data["current_password"],
            new_password=serializer.validated_data["new_password"],
        )

        if not result["success"]:
            if result.get("locked"):
                return Response(
                    {
                        "message": result["message"],
                        "code": result["code"],
                        "retry_after_seconds": result["retry_after_seconds"],
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            return Response(
                {"message": result["message"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # The other devices' tokens are revoked, so this device is handed
        # replacements to keep its own session.
        return Response(
            {"message": result["message"], **result["tokens"]},
            status=status.HTTP_200_OK,
        )
