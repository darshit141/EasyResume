from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.serializers.verify_login_otp_serializer import (
    VerifyLoginOTPSerializer,
)
from authentication.services.verify_login_otp_service import (
    VerifyLoginOTPService,
)
from drf_spectacular.utils import (
    extend_schema,
    OpenApiResponse,
)


@extend_schema(
    request=VerifyLoginOTPSerializer,
    responses={
        200: OpenApiResponse(
            description="Login successful. JWT tokens returned."
        ),
        401: OpenApiResponse(
            description="Invalid OTP or OTP has expired. code: OTP_INVALID."
        ),
        429: OpenApiResponse(
            description="Too many wrong codes; the code is void. code: OTP_ATTEMPTS_EXCEEDED."
        ),
        404: OpenApiResponse(
            description="User not found or OTP not found."
        ),
    },
)
class VerifyLoginOTPAPIView(APIView):
    """
    API for verifying login OTP.
    """

    throttle_scope = "auth_sensitive"

    def post(self, request):
        serializer = VerifyLoginOTPSerializer(data=request.data)

        if serializer.is_valid():

            result = VerifyLoginOTPService.verify_otp(
                email=serializer.validated_data["email"],
                otp=serializer.validated_data["otp"],
            )

            if not result["success"]:
                body = {"message": result["message"]}
                if result.get("code"):
                    body["code"] = result["code"]
                    body["attempts_remaining"] = result.get("attempts_remaining")
                return Response(body, status=result["status"])
            user = result["user"]

            return Response(
                {
                    "message": result["message"],
                    "access": result["access"],
                    "refresh": result["refresh"],
                    "user": {
                        "id": user.id,
                        "name": user.name,
                        "email": user.email,
                        "phone": user.phone,
                        "is_staff": user.is_staff,
                    },
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )