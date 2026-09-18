"""Endpoints for a user's own provider API key.

Every response from this module is safe to show in a browser's network tab:
none of them contains a key, or any part of one beyond the last four
characters the user needs in order to recognise which credential is saved.
"""

import logging

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.serializers.billing_serializer import (
    BillingErrorSerializer,
    UserAIKeySummarySerializer,
    UserAIKeyToggleSerializer,
    UserAIKeyWriteSerializer,
)
from billing.services.user_ai_key_service import (
    BYOKDisabledError,
    UserAIKeyError,
    UserAIKeyService,
)

logger = logging.getLogger(__name__)


def _error(exc):
    code = status.HTTP_400_BAD_REQUEST
    if isinstance(exc, BYOKDisabledError):
        code = status.HTTP_403_FORBIDDEN
    elif exc.code == "PROVIDER_UNREACHABLE":
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Response({"detail": exc.message, "code": exc.code}, status=code)


@extend_schema(
    tags=["Billing"],
    summary="Manage your own AI provider API key",
    description=(
        "Optional. IntelliResume's own AI infrastructure serves every "
        "request by default; a key saved here is used instead for that "
        "provider, and while one is active the monthly AI-action ceiling — "
        "which exists only to cap IntelliResume's provider spend — no longer "
        "applies. It does not unlock paid plan features.\n\n"
        "Keys are validated against the provider before they are stored, "
        "encrypted at rest, and never returned by any endpoint."
    ),
)
class UserAIKeyAPIView(APIView):
    permission_classes = [IsAuthenticated]
    # Saving a key calls the provider to validate it, so an unthrottled
    # caller could use this endpoint to probe stolen keys against Google at
    # our expense and from our IP. Only the scope is declared here — the
    # ScopedRateThrottle class itself comes from DEFAULT_THROTTLE_CLASSES,
    # which is deliberately empty under `manage.py test`. Naming the class
    # on the view would override that and make every test in this file
    # share one wall-clock rate window.
    throttle_scope = "user_ai_key"

    @extend_schema(responses={status.HTTP_200_OK: UserAIKeySummarySerializer})
    def get(self, request):
        return Response(UserAIKeyService.summary(request.user))

    @extend_schema(
        request=UserAIKeyWriteSerializer,
        responses={
            status.HTTP_200_OK: UserAIKeySummarySerializer,
            status.HTTP_400_BAD_REQUEST: OpenApiResponse(response=BillingErrorSerializer),
            status.HTTP_403_FORBIDDEN: OpenApiResponse(
                response=BillingErrorSerializer,
                description="Bring-your-own-key is switched off on this server.",
            ),
            status.HTTP_503_SERVICE_UNAVAILABLE: OpenApiResponse(
                response=BillingErrorSerializer,
                description="The provider could not be reached to validate the key.",
            ),
        },
    )
    def put(self, request):
        serializer = UserAIKeyWriteSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            UserAIKeyService.set_key(
                request.user,
                serializer.validated_data["provider"],
                serializer.validated_data["api_key"],
            )
        except UserAIKeyError as exc:
            return _error(exc)
        return Response(UserAIKeyService.summary(request.user))

    @extend_schema(
        request=UserAIKeyToggleSerializer,
        responses={status.HTTP_200_OK: UserAIKeySummarySerializer},
    )
    def patch(self, request):
        """Pause or resume a saved key without having to find it again."""
        serializer = UserAIKeyToggleSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            UserAIKeyService.set_active(
                request.user,
                serializer.validated_data["provider"],
                serializer.validated_data["is_active"],
            )
        except UserAIKeyError as exc:
            return _error(exc)
        return Response(UserAIKeyService.summary(request.user))

    @extend_schema(
        responses={status.HTTP_200_OK: UserAIKeySummarySerializer},
        description=(
            "Deletes the stored key. Deliberately allowed even when "
            "bring-your-own-key is switched off server-wide: a user must "
            "always be able to withdraw a credential they gave us."
        ),
    )
    def delete(self, request):
        provider = request.query_params.get("provider") or request.data.get("provider")
        if not provider:
            return Response(
                {"detail": "A provider is required.", "code": "PROVIDER_REQUIRED"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        UserAIKeyService.clear_key(request.user, provider)
        return Response(UserAIKeyService.summary(request.user))
