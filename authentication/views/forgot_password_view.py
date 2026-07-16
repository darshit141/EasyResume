from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.serializers.forgot_password_serializer import (
    ForgotPasswordSerializer,
)
from authentication.services.forgot_password_service import (
    ForgotPasswordService,
)


class ForgotPasswordAPIView(APIView):
    """
    API for forgot password.
    """

    def post(self, request):
        serializer = ForgotPasswordSerializer(data=request.data)

        if serializer.is_valid():

            otp = ForgotPasswordService.send_otp(
                serializer.validated_data["email"]
            )

            if otp is None:
                return Response(
                    {
                        "message": "User with this email does not exist."
                    },
                    status=status.HTTP_404_NOT_FOUND,
                )

            # Temporary response until email sending is implemented
            return Response(
                {
                    "message": "OTP sent successfully."
                },
                status=status.HTTP_200_OK,
            )

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )