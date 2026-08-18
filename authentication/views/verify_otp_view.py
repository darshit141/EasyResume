from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.serializers.verify_otp_serializer import (
    VerifyOTPSerializer,
)
from authentication.services.verify_otp_service import (
    VerifyOTPService,
)
from drf_spectacular.utils import extend_schema

@extend_schema(
    request=VerifyOTPSerializer,
)
class VerifyOTPAPIView(APIView):
    """
    API for OTP verification.
    """

    throttle_scope = "auth_sensitive"

    def post(self, request):
        serializer = VerifyOTPSerializer(data=request.data)

        if serializer.is_valid():

            result = VerifyOTPService.verify_otp(
                email=serializer.validated_data["email"],
                otp=serializer.validated_data["otp"],
            )

            if not result["success"]:
                return Response(
                    {
                        "message": result["message"],
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            return Response(
                {
                    "message": result["message"],
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )