from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.serializers.b2b_lead_serializers import B2BLeadRequestSerializer


@extend_schema(
    tags=["Leads"],
    summary="Submit a B2B / career-coach lead",
    description=(
        "Public form submission from /for-career-coaches — a coach or "
        "agency asking about a bulk/white-label arrangement, not a "
        "self-service purchase. Stored for manual sales follow-up; visible "
        "and triaged entirely through Django admin, no confirmation email "
        "is sent automatically."
    ),
    request=B2BLeadRequestSerializer,
    responses={
        status.HTTP_201_CREATED: OpenApiResponse(description="Recorded."),
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(description="Validation error."),
    },
)
class B2BLeadCreateAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "b2b_lead"

    def post(self, request):
        serializer = B2BLeadRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        serializer.save()
        return Response(status=status.HTTP_201_CREATED)
