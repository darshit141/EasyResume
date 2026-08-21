from django.conf import settings
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.models import CookieConsent
from authentication.serializers.cookie_consent_serializer import (
    CookieConsentRequestSerializer,
    CookieConsentSerializer,
)


@extend_schema(
    tags=["Account"],
    summary="Get / set the authenticated user's cookie consent",
    description=(
        "The signed-in-user counterpart to the browser-side cookie "
        "banner's localStorage preference — lets a logged-in user's "
        "choice stay consistent across devices and gives a durable audit "
        "record. `necessary` is not a field: it is never actually "
        "optional. GET 404s if the user has never set a preference "
        "(the frontend falls back to its local default of "
        "analytics/marketing both off). PUT always creates a fresh "
        "record of the current consent_version — a changed preference is "
        "an update to the *current* state, not a new history row, unlike "
        "policy acceptance."
    ),
    responses={
        status.HTTP_200_OK: CookieConsentSerializer,
        status.HTTP_404_NOT_FOUND: OpenApiResponse(description="No preference recorded yet."),
    },
)
class CookieConsentAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        consent = CookieConsent.objects.filter(user=request.user).first()
        if consent is None:
            return Response(
                {"detail": "No cookie preference recorded yet."}, status=status.HTTP_404_NOT_FOUND
            )
        return Response(CookieConsentSerializer(consent).data)

    @extend_schema(
        request=CookieConsentRequestSerializer,
        responses={status.HTTP_200_OK: CookieConsentSerializer},
    )
    def put(self, request):
        serializer = CookieConsentRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        consent, _ = CookieConsent.objects.update_or_create(
            user=request.user,
            defaults={
                "analytics": serializer.validated_data["analytics"],
                "marketing": serializer.validated_data["marketing"],
                "consent_version": settings.CURRENT_COOKIE_POLICY_VERSION,
            },
        )
        return Response(CookieConsentSerializer(consent).data)
