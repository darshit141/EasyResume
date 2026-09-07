"""Tests for admin-selectable display-ad providers.

The behaviour worth defending: a half-configured or unrecognised provider
serves *nothing*, rather than falling through to whichever network happens
to have an id set. Serving ads an operator didn't choose is worse than
serving none.
"""

from django.test import TestCase, override_settings

from api.models import SystemConfiguration
from billing.services.display_ad_service import DisplayAdService

URL = "/api/billing/ads/config/"


def config(key, value, value_type="string"):
    SystemConfiguration.objects.update_or_create(
        key=key,
        defaults={"value": value, "value_type": value_type, "category": "ads", "description": ""},
    )


@override_settings(
    DISPLAY_ADS_ENABLED="False",
    DISPLAY_AD_PROVIDER="adsense",
    ADSENSE_CLIENT_ID="",
    GAM_NETWORK_CODE="",
    DISPLAY_AD_UNIT_ARTICLE_TOP="",
    DISPLAY_AD_UNIT_ARTICLE_BOTTOM="",
    DISPLAY_AD_UNIT_BLOG_INDEX="",
)
class DisplayAdServiceTests(TestCase):
    def enable_adsense(self):
        config("DISPLAY_ADS_ENABLED", "true", "boolean")
        config("DISPLAY_AD_PROVIDER", "adsense")
        config("ADSENSE_CLIENT_ID", "ca-pub-123")
        config("DISPLAY_AD_UNIT_ARTICLE_TOP", "9988")

    def test_disabled_by_default(self):
        result = DisplayAdService.config()

        self.assertFalse(result["enabled"])
        self.assertEqual(result["provider"], "none")
        self.assertEqual(result["units"], {})

    def test_adsense_config_is_served_when_complete(self):
        self.enable_adsense()

        result = DisplayAdService.config()

        self.assertTrue(result["enabled"])
        self.assertEqual(result["provider"], "adsense")
        self.assertEqual(result["client_id"], "ca-pub-123")
        self.assertEqual(result["units"], {"article_top": "9988"})
        # The other provider's id must not leak into an AdSense config.
        self.assertEqual(result["network_code"], "")

    def test_switching_provider_from_the_admin_tab_changes_what_is_served(self):
        """The whole point of the feature: no rebuild, no redeploy."""
        self.enable_adsense()
        config("DISPLAY_AD_PROVIDER", "admanager")
        config("GAM_NETWORK_CODE", "6355419")
        config("DISPLAY_AD_UNIT_ARTICLE_TOP", "/6355419/article-top")

        result = DisplayAdService.config()

        self.assertEqual(result["provider"], "admanager")
        self.assertEqual(result["network_code"], "6355419")
        self.assertEqual(result["client_id"], "")
        self.assertEqual(result["units"]["article_top"], "/6355419/article-top")

    def test_master_switch_off_beats_a_complete_provider_config(self):
        self.enable_adsense()
        config("DISPLAY_ADS_ENABLED", "false", "boolean")

        self.assertFalse(DisplayAdService.config()["enabled"])

    def test_provider_none_serves_nothing_even_when_ids_are_set(self):
        self.enable_adsense()
        config("DISPLAY_AD_PROVIDER", "none")

        self.assertFalse(DisplayAdService.config()["enabled"])

    def test_unrecognised_provider_serves_nothing_rather_than_guessing(self):
        self.enable_adsense()
        config("DISPLAY_AD_PROVIDER", "some-network-we-never-integrated")

        result = DisplayAdService.config()

        self.assertFalse(result["enabled"])
        self.assertEqual(result["provider"], "none")

    def test_missing_account_id_disables_rather_than_rendering_dead_space(self):
        self.enable_adsense()
        config("ADSENSE_CLIENT_ID", "")

        self.assertFalse(DisplayAdService.config()["enabled"])

    def test_missing_units_disables(self):
        self.enable_adsense()
        config("DISPLAY_AD_UNIT_ARTICLE_TOP", "")

        self.assertFalse(DisplayAdService.config()["enabled"])

    def test_admanager_selected_without_a_network_code_is_disabled(self):
        config("DISPLAY_ADS_ENABLED", "true", "boolean")
        config("DISPLAY_AD_PROVIDER", "admanager")
        config("ADSENSE_CLIENT_ID", "ca-pub-123")  # the *other* provider's id
        config("DISPLAY_AD_UNIT_ARTICLE_TOP", "/x/y")

        self.assertFalse(DisplayAdService.config()["enabled"])

    def test_only_configured_placements_are_returned(self):
        self.enable_adsense()
        config("DISPLAY_AD_UNIT_BLOG_INDEX", "4321")

        units = DisplayAdService.config()["units"]

        self.assertEqual(set(units), {"article_top", "blog_index"})


@override_settings(
    DISPLAY_ADS_ENABLED="False",
    DISPLAY_AD_PROVIDER="adsense",
    ADSENSE_CLIENT_ID="",
    GAM_NETWORK_CODE="",
    DISPLAY_AD_UNIT_ARTICLE_TOP="",
    DISPLAY_AD_UNIT_ARTICLE_BOTTOM="",
    DISPLAY_AD_UNIT_BLOG_INDEX="",
)
class DisplayAdConfigAPITests(TestCase):
    def test_endpoint_is_public(self):
        """Blog and marketing pages are read by signed-out visitors, so an
        authenticated-only config would mean no ads for exactly the audience
        the ads are for."""
        response = self.client.get(URL)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["enabled"])

    def test_endpoint_reports_the_selected_provider(self):
        config("DISPLAY_ADS_ENABLED", "true", "boolean")
        config("DISPLAY_AD_PROVIDER", "adsense")
        config("ADSENSE_CLIENT_ID", "ca-pub-777")
        config("DISPLAY_AD_UNIT_BLOG_INDEX", "111")

        response = self.client.get(URL)

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["enabled"])
        self.assertEqual(response.data["provider"], "adsense")
        self.assertEqual(response.data["client_id"], "ca-pub-777")
        self.assertEqual(response.data["units"], {"blog_index": "111"})

    def test_response_shape_is_stable_when_disabled(self):
        """The client destructures this unconditionally, so the keys must be
        present even with ads off."""
        response = self.client.get(URL)

        self.assertEqual(
            set(response.data),
            {"enabled", "provider", "client_id", "network_code", "units"},
        )
