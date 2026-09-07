from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from billing.services.display_ad_service import DisplayAdService


@extend_schema(
    tags=["Billing"],
    summary="Display-ad configuration for the browser",
    description=(
        "Which banner network to serve, and the ids it needs. Read by the "
        "frontend's ad slots so the network can be switched from the admin "
        "Configuration tab without rebuilding the frontend — the ad client "
        "used to be compiled into the bundle, which made choosing a provider "
        "impossible without a redeploy.\n\n"
        "Deliberately public: banner slots appear on the blog and marketing "
        "pages, which signed-out visitors see. Nothing returned here is "
        "secret — an ad client id, network code and ad unit path are all "
        "published in the page's own HTML for the network to read.\n\n"
        "When ads are off, or the selected provider is missing an id it "
        "needs, `enabled` is false and every other field is empty; the "
        "client then shows its own upgrade prompt rather than leaving a gap. "
        "Whether a *particular viewer* sees an ad also depends on their plan "
        "and cookie consent, both of which the client already enforces."
    ),
    responses={
        status.HTTP_200_OK: OpenApiResponse(
            description=(
                "{enabled, provider, client_id, network_code, units{placement: id}}"
            )
        )
    },
)
class DisplayAdConfigAPIView(APIView):
    permission_classes = [AllowAny]
    # No throttle scope: this is a tiny cached-shaped read that every blog
    # page needs, including for anonymous visitors. Rate limiting it would
    # break the page for a burst of legitimate readers to protect data that
    # is public by nature.
    throttle_classes = []

    def get(self, request):
        return Response(DisplayAdService.config())
