from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.serializers.logout_serializer import LogoutSerializer
from authentication.services.logout_service import LogoutService


@extend_schema(
    tags=["Account"],
    summary="Log out (revoke refresh token)",
    description=(
        "Blacklists the submitted refresh token so it — and any access "
        "token minted from it after this call — can no longer be used. "
        "Clients must still discard locally stored tokens themselves; this "
        "call is what makes logout effective server-side rather than only "
        "clearing client storage."
    ),
    request=LogoutSerializer,
    responses={
        status.HTTP_200_OK: OpenApiResponse(description="Logged out successfully."),
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(
            description="Missing, invalid, or already-invalidated refresh token."
        ),
        status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
    },
)
class LogoutAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = LogoutSerializer(data=request.data)

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        result = LogoutService.logout(serializer.validated_data["refresh"])

        if not result["success"]:
            return Response(
                {"message": result["message"]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response({"message": result["message"]}, status=status.HTTP_200_OK)
