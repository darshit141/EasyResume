from drf_spectacular.utils import OpenApiResponse, extend_schema_view, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.serializers.profile_serializer import (
    UpdateProfileSerializer,
    UserProfileSerializer,
)
from authentication.services.profile_service import ProfileService


@extend_schema_view(
    get=extend_schema(
        tags=["Account"],
        summary="Get the authenticated user's profile",
        description="Returns the authenticated user's id, name, email, and phone.",
        responses={
            status.HTTP_200_OK: UserProfileSerializer,
            status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
        },
    ),
    patch=extend_schema(
        tags=["Account"],
        summary="Update the authenticated user's profile",
        description=(
            "Updates name and/or phone only — email is read-only here "
            "since it is the unique sign-in identifier and changing it "
            "would require a re-verification flow that does not exist yet."
        ),
        request=UpdateProfileSerializer,
        responses={
            status.HTTP_200_OK: UserProfileSerializer,
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(description="Validation error."),
            status.HTTP_401_UNAUTHORIZED: OpenApiResponse(description="Authentication required."),
        },
    ),
)
class ProfileAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(UserProfileSerializer(request.user).data)

    def patch(self, request):
        serializer = UpdateProfileSerializer(
            instance=request.user,
            data=request.data,
            partial=True,
        )

        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        ProfileService.update_profile(request.user, serializer.validated_data)

        return Response(UserProfileSerializer(request.user).data)
