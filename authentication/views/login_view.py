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
                body = {"message": result["message"]}
                if result.get("code"):
                    body["code"] = result["code"]
                    body["retry_after_seconds"] = result.get("retry_after_seconds")
                response = Response(body, status=result["status"])
                if result.get("retry_after_seconds"):
                    response["Retry-After"] = str(result["retry_after_seconds"])
                return response

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

            # Two-factor: password accepted, a second factor is pending and
            # tokens come from the verify-otp step. otp_method tells the
            # client which prompt to show: "email" (a code was mailed) or
            # "totp" (the user's authenticator app).
            return Response(
                {
                    "success": result["success"],
                    "otp_required": True,
                    "otp_method": result["otp_method"],
                    "message": result["message"],
                    "email": result["email"],
                    "resend_available_in": result["resend_available_in"],
                    "expires_in_seconds": result["expires_in_seconds"],
                },
                status=result["status"],
            )

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )