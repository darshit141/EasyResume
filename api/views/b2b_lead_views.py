from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import permissions, status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.models import B2BLead
from api.serializers.b2b_lead_serializers import B2BLeadAdminSerializer, B2BLeadRequestSerializer


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


class AdminB2BLeadListAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        leads = B2BLead.objects.all().order_by("-created_at")
        return Response(B2BLeadAdminSerializer(leads, many=True).data)


class AdminB2BLeadToggleContactedAPIView(APIView):
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, lead_id):
        try:
            lead = B2BLead.objects.get(id=lead_id)
        except B2BLead.DoesNotExist:
            return Response({"detail": "Lead not found."}, status=status.HTTP_404_NOT_FOUND)

        lead.contacted = not lead.contacted
        lead.save(update_fields=["contacted"])
        return Response(B2BLeadAdminSerializer(lead).data)
