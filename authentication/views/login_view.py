from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.serializers.login_serializer import LoginSerializer
from authentication.services.login_service import LoginService


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

            if result is None:
                return Response(
                    {
                        "message": "Invalid email or password."
                    },
                    status=status.HTTP_401_UNAUTHORIZED,
                )

            user = result["user"]

            return Response(
                {
                    "message": "Login successful.",
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