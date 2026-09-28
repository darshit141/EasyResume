from django.conf import settings
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.utils.config import get_config_value

# Must match the options in DEFAULT_CONFIGS and the images the frontend ships.
HERO_VISUALS = ("example", "hero-assembly", "hero-evolution", "hero-workspace")


def hero_visual():
    value = (get_config_value("HOMEPAGE_HERO_VISUAL", settings.HOMEPAGE_HERO_VISUAL) or "").strip()
    # An unknown value (a typo, or an option removed from the frontend) would
    # leave the hero with a broken image; the fallback always has one.
    return value if value in HERO_VISUALS else settings.HOMEPAGE_HERO_VISUAL


class SiteConfigAPIView(APIView):
    """Presentation settings for public pages, read by the homepage.

    Public because signed-out visitors see the homepage. Nothing here is
    sensitive: it only names which bundled illustration to show.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    # A tiny read every homepage visit makes; throttling it would break the
    # page for a burst of real visitors to protect public data.
    throttle_classes = []

    def get(self, request):
        return Response({"hero_visual": hero_visual()})
