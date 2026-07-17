from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.serializers.reset_password_serializer import (
    ResetPasswordSerializer,
)
from authentication.services.reset_password_service import (
    ResetPasswordService,
)
from drf_spectacular.utils import extend_schema

@extend_schema(
    request=ResetPasswordSerializer,
)
class ResetPasswordAPIView(APIView):
    """
    API for resetting user password.
    """

    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)

        if serializer.is_valid():

            result = ResetPasswordService.reset_password(
                email=serializer.validated_data["email"],
                password=serializer.validated_data["password"],
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