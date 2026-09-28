"""The homepage hero illustration is chosen in the admin panel.

Driven through SystemConfiguration, which is what the admin Configuration tab
writes, so the tests take the same path a real change does.
"""

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from api.models import SystemConfiguration
from api.views.admin_views import DEFAULT_CONFIGS
from api.views.site_views import HERO_VISUALS

URL = "/api/site/config/"


def set_hero(value):
    SystemConfiguration.objects.update_or_create(
        key="HOMEPAGE_HERO_VISUAL",
        defaults={"value": value, "value_type": "string", "category": "site", "description": ""},
    )


@override_settings(HOMEPAGE_HERO_VISUAL="hero-assembly")
class SiteConfigTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_public_and_uses_the_default(self):
        response = self.client.get(URL)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"hero_visual": "hero-assembly"})

    def test_admin_choice_applies_without_redeploy(self):
        set_hero("hero-workspace")
        self.assertEqual(self.client.get(URL).json()["hero_visual"], "hero-workspace")
        set_hero("example")
        self.assertEqual(self.client.get(URL).json()["hero_visual"], "example")

    def test_unknown_value_falls_back_instead_of_breaking_the_hero(self):
        set_hero("hero-typo")
        self.assertEqual(self.client.get(URL).json()["hero_visual"], "hero-assembly")

    def test_admin_choices_match_what_the_endpoint_accepts(self):
        meta = next(d for d in DEFAULT_CONFIGS if d["key"] == "HOMEPAGE_HERO_VISUAL")
        self.assertEqual(tuple(c["value"] for c in meta["choices"]), HERO_VISUALS)
