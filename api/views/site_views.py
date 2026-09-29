from django.conf import settings
from django.http import Http404, HttpResponse
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from api.models import SiteImage
from api.site_images import SLOTS, builtins_for, normalise_builtin
from api.utils.config import get_config_value

THEMES = ("indigo", "ocean", "emerald", "violet", "rose", "sunset")
COLOR_MODES = ("system", "light", "dark")


def _choice(key, allowed):
    value = (get_config_value(key, getattr(settings, key)) or "").strip()
    # An unknown value (a typo, or an option since removed) falls back rather
    # than leaving the site unstyled.
    return value if value in allowed else getattr(settings, key)


def image_src(row):
    return f"site/images/{row.slot}/?v={int(row.updated_at.timestamp())}"


def image_override(row):
    """What the frontend shows in place of the slot's default, or None to hide it.

    Returns False when the row can't be honoured (a bundled name that no
    longer exists), so the caller leaves the default in place.
    """
    if row.source == SiteImage.SOURCE_HIDDEN:
        return None
    if row.source == SiteImage.SOURCE_CUSTOM:
        if not row.image:
            return False
        return {"src": image_src(row), "width": row.width, "height": row.height, "alt": row.alt_text}
    builtin = normalise_builtin(row.builtin)
    if builtin not in builtins_for(row.slot):
        return False
    override = {"builtin": builtin}
    if row.alt_text:
        override["alt"] = row.alt_text
    return override


def site_images():
    images = {}
    for row in SiteImage.objects.filter(slot__in=SLOTS).defer("image"):
        override = image_override(row)
        if override is not False:
            images[row.slot] = override
    return images


def legacy_hero_visual(images):
    """The pre-slot `hero_visual` field, for pages loaded before this release."""
    hero = images.get("home_hero")
    if hero and hero.get("builtin") in ("example", "hero-assembly", "hero-evolution", "hero-workspace"):
        return hero["builtin"]
    return "hero-full-loop"


class SiteConfigAPIView(APIView):
    """Presentation settings for public pages: the colour theme and any image
    the admin has changed from its default.

    Public because signed-out visitors see these pages. Nothing here is
    sensitive: preset names and picture URLs.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    # A tiny read every page load makes; throttling it would break the page
    # for a burst of real visitors to protect public data.
    throttle_classes = []

    def get(self, request):
        images = site_images()
        return Response({
            "theme": {
                "preset": _choice("SITE_THEME", THEMES),
                "default_mode": _choice("SITE_DEFAULT_COLOR_MODE", COLOR_MODES),
            },
            "images": images,
            "hero_visual": legacy_hero_visual(images),
        })


class SiteImageFileAPIView(APIView):
    """The bytes of an uploaded site image.

    The URL carries a version (?v=<updated_at>) that changes on every upload,
    so the response can be cached for good.
    """

    permission_classes = [AllowAny]
    authentication_classes = []
    throttle_classes = []

    def get(self, request, slot):
        row = SiteImage.objects.filter(slot=slot, source=SiteImage.SOURCE_CUSTOM).first()
        if row is None or not row.image or slot not in SLOTS:
            raise Http404
        response = HttpResponse(bytes(row.image), content_type=row.content_type or "image/webp")
        response["Cache-Control"] = "public, max-age=31536000, immutable"
        # The site and the API live on different origins.
        response["Cross-Origin-Resource-Policy"] = "cross-origin"
        response["X-Content-Type-Options"] = "nosniff"
        return response
