import logging

from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.serializers.feedback_serializers import FeedbackRequestSerializer
from api.services.feedback_mailer import send_feedback_email

logger = logging.getLogger(__name__)


@extend_schema(
    tags=["Feedback"],
    summary="Report a bug, request a feature or send a contact message",
    description=(
        "Forwarded straight to the sales inbox for manual triage — there is "
        "no ticket queue behind this, just an email. Open to a signed-out "
        "visitor as well as a logged-in user; a logged-in user's account "
        "email/name is used automatically so replying reaches them without "
        "asking them to type it in."
    ),
    request=FeedbackRequestSerializer,
    responses={
        status.HTTP_201_CREATED: OpenApiResponse(description="Sent."),
        status.HTTP_400_BAD_REQUEST: OpenApiResponse(description="Validation error."),
        status.HTTP_502_BAD_GATEWAY: OpenApiResponse(description="The report could not be delivered."),
    },
)
class FeedbackCreateAPIView(APIView):
    permission_classes = [AllowAny]
    throttle_scope = "feedback"

    def post(self, request):
        serializer = FeedbackRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        user = request.user if request.user.is_authenticated else None
        reporter_email = user.email if user else data.get("email", "")
        reporter_name = user.name if user else data.get("name", "")

        # A contact message is a question that needs an answer, so an
        # anonymous sender has to leave somewhere to send it. A bug report
        # is still useful without one.
        if data["category"] == "contact" and not reporter_email:
            return Response(
                {"email": ["Please enter your email so we can reply."]},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            send_feedback_email(
                category=data["category"],
                message=data["message"],
                reporter_email=reporter_email,
                reporter_name=reporter_name,
                page_url=data.get("page_url", ""),
                user_id=user.id if user else None,
            )
        except Exception:
            logger.exception("Failed to deliver a feedback report.")
            return Response(
                {"detail": "Couldn't send your report right now. Please try again shortly."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        return Response(status=status.HTTP_201_CREATED)
