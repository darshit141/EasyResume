from django.conf import settings
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.models import PolicyAcceptance
from authentication.serializers.policy_acceptance_serializer import (
    PolicyAcceptanceRequestSerializer,
    PolicyAcceptanceSerializer,
)

_CURRENT_VERSION_BY_POLICY_TYPE = {
    PolicyAcceptance.POLICY_PRIVACY: lambda: settings.CURRENT_PRIVACY_POLICY_VERSION,
    PolicyAcceptance.POLICY_TERMS: lambda: settings.CURRENT_TERMS_OF_SERVICE_VERSION,
    PolicyAcceptance.POLICY_COOKIE: lambda: settings.CURRENT_COOKIE_POLICY_VERSION,
}


@extend_schema(
    tags=["Account"],
    summary="Record / list policy acceptance",
    description=(
        "GET returns the authenticated user's full acceptance history "
        "(every version of every policy they've ever accepted, newest "
        "first) — never overwritten, always appended to. POST records "
        "acceptance of the *current* server-configured version of the "
        "named policy type as a new row."
    ),
    responses={status.HTTP_200_OK: PolicyAcceptanceSerializer(many=True)},
)
class PolicyAcceptanceAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        acceptances = request.user.policy_acceptances.all()
        return Response(PolicyAcceptanceSerializer(acceptances, many=True).data)

    @extend_schema(
        request=PolicyAcceptanceRequestSerializer,
        responses={
            status.HTTP_201_CREATED: PolicyAcceptanceSerializer,
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(description="Validation error."),
        },
    )
    def post(self, request):
        serializer = PolicyAcceptanceRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        policy_type = serializer.validated_data["policy_type"]
        version = _CURRENT_VERSION_BY_POLICY_TYPE[policy_type]()
        acceptance = PolicyAcceptance.objects.create(
            user=request.user, policy_type=policy_type, version=version
        )
        return Response(
            PolicyAcceptanceSerializer(acceptance).data, status=status.HTTP_201_CREATED
        )
