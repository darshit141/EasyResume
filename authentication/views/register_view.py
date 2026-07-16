from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.serializers.register_serializer import RegisterSerializer
from authentication.services.register_service import RegisterService


class RegisterAPIView(APIView):
    """
    API for user registration.
    """

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)

        if serializer.is_valid():
            user = RegisterService.register_user(
                serializer.validated_data
            )

            return Response(
                {
                    "message": "User registered successfully.",
                    "user": {
                        "id": user.id,
                        "name": user.name,
                        "email": user.email,
                        "phone": user.phone,
                    },
                },
                status=status.HTTP_201_CREATED,
            )

        return Response(
            serializer.errors,
            status=status.HTTP_400_BAD_REQUEST,
        )