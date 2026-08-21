from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.models import AnalyticsEvent
from api.serializers.analytics_serializers import TrackEventRequestSerializer


@extend_schema(
    tags=["Analytics"],
    summary="Track a funnel/acquisition event",
    description=(
        "Records one of a small, fixed set of funnel events (landing page "
        "view, signup completed, checkout started) — see "
        "COMMERCIAL_READINESS_AUDIT.md §20/§21. Public and best-effort: "
        "the frontend fires this without blocking on the response and "
        "swallows failures, so this must never be relied on as the source "
        "of truth for anything the product itself depends on (billing "
        "state comes from Stripe webhooks, not this). Associates with the "
        "authenticated user when there is one, otherwise with the "
        "supplied `session_id` (a client-generated id, not personally "
        "identifying on its own) so an anonymous visitor's pre-signup "
        "steps still count toward the funnel."
    ),
    request=TrackEventRequestSerializer,
    responses={
        status.HTTP_201_CREATED: OpenApiResponse(description="Recorded."),
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(description="Validation error."),
    },
)
class TrackEventAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "analytics_track"

    def post(self, request):
        serializer = TrackEventRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        AnalyticsEvent.objects.create(
            event_type=serializer.validated_data["event_type"],
            user=request.user if request.user.is_authenticated else None,
            session_id=serializer.validated_data.get("session_id", ""),
            metadata=serializer.validated_data.get("metadata", {}),
        )
        return Response(status=status.HTTP_201_CREATED)
