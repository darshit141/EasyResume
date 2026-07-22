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

            # Login successful
            return Response(
                {
                    "success": result["success"],
                    "message": result["message"],
                    "email": result["email"],
                },
                status=result["status"],
            )

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )