from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.serializers.login_serializer import LoginSerializer
from authentication.services.login_service import LoginService
from drf_spectacular.utils import extend_schema


@extend_schema(
    request=LoginSerializer,
)
class LoginAPIView(APIView):
    """
    API for user login.
    """

    throttle_scope = "auth_sensitive"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)

        if serializer.is_valid():

            result = LoginService.login_user(
                email=serializer.validated_data["email"],
                password=serializer.validated_data["password"],
            )

            # Login failed
            if not result["success"]:
                return Response(
                    {
                        "message": result["message"],
                    },
                    status=result["status"],
                )

            # Single-factor login (OTP disabled by an admin): tokens are
            # issued here instead of at the verify-otp step. Same response
            # shape as VerifyLoginOTPAPIView so the frontend has exactly one
            # "you are logged in" contract to handle, not two.
            if not result.get("otp_required", True):
                user = result["user"]
                return Response(
                    {
                        "success": True,
                        "otp_required": False,
                        "message": result["message"],
                        "email": result["email"],
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
                    status=result["status"],
                )

            # Two-factor: password accepted, OTP sent, tokens come later.
            return Response(
                {
                    "success": result["success"],
                    "otp_required": True,
                    "message": result["message"],
                    "email": result["email"],
                },
                status=result["status"],
            )

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )