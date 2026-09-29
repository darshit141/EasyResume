from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.serializers.forgot_password_serializer import (
    ForgotPasswordSerializer,
)
from authentication.services.forgot_password_service import (
    ForgotPasswordService,
)

from drf_spectacular.utils import extend_schema

@extend_schema(
    request=ForgotPasswordSerializer,
)
class ForgotPasswordAPIView(APIView):
    """
    API for forgot password.
    """

    throttle_scope = "auth_sensitive"

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)

        if serializer.is_valid():

            ForgotPasswordService.send_otp(
                serializer.validated_data["email"]
            )

            # The same answer whether or not the email has an account:
            # "User does not exist" would turn this endpoint into a free
            # lookup of who is signed up.
            return Response(
                {
                    "message": "If an account exists for this email, we've sent a code to it."
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )