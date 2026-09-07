"""Which display-ad network serves the banner slots, and with what ids.

Companion to ``rewarded_ad_providers``: that one decides who serves the
*rewarded video* a free user watches to earn a credit, this one decides who
serves the ordinary banner placements on marketing and blog pages.

The important difference from how display ads worked before: every value
here is resolved through ``SystemConfiguration`` (the admin Configuration
tab), not from ``VITE_*`` build-time environment variables. Baking the ad
client into the JavaScript bundle meant "let the admin choose the provider"
was impossible by construction — switching networks required editing an env
file and rebuilding the frontend. Serving this from the API instead makes
the choice a setting, which is what it always should have been.

Nothing here is secret. An ad client id, a network code and an ad unit path
are all published in the page's own HTML for the network to read; they are
configuration, not credentials. Genuine secrets (the rewarded-ad S2S key,
LLM keys) stay in the environment exactly as before.
"""

import logging

from django.conf import settings

from api.utils.config import get_config_value

logger = logging.getLogger(__name__)

PROVIDER_ADSENSE = "adsense"
PROVIDER_AD_MANAGER = "admanager"
PROVIDER_NONE = "none"

SUPPORTED_PROVIDERS = (PROVIDER_ADSENSE, PROVIDER_AD_MANAGER, PROVIDER_NONE)

# The placements the frontend actually has. Named rather than numbered so a
# call site reads as a position on the page, and so switching networks does
# not mean re-threading ids through components.
PLACEMENTS = ("article_top", "article_bottom", "blog_index")

_UNIT_CONFIG_KEYS = {
    placement: f"DISPLAY_AD_UNIT_{placement.upper()}" for placement in PLACEMENTS
}


class DisplayAdService:
    @staticmethod
    def provider():
        raw = (get_config_value("DISPLAY_AD_PROVIDER", settings.DISPLAY_AD_PROVIDER) or "").strip().lower()
        if raw not in SUPPORTED_PROVIDERS:
            # An unrecognised value must not silently fall through to a
            # working network — that would serve ads the operator did not
            # choose. Off is the safe reading of "I don't know what this is".
            if raw:
                logger.warning("Unknown DISPLAY_AD_PROVIDER %r; serving no ads.", raw)
            return PROVIDER_NONE
        return raw

    @staticmethod
    def enabled():
        return bool(get_config_value("DISPLAY_ADS_ENABLED", settings.DISPLAY_ADS_ENABLED))

    @staticmethod
    def units():
        resolved = {}
        for placement, key in _UNIT_CONFIG_KEYS.items():
            value = (get_config_value(key, getattr(settings, key, "")) or "").strip()
            if value:
                resolved[placement] = value
        return resolved

    @staticmethod
    def config():
        """The public, browser-facing ad configuration.

        Always returns a complete shape. When ads are off, or the selected
        provider is missing the account id it needs, `enabled` is False and
        the client renders its in-house upgrade prompt instead — a half
        configured network would otherwise leave dead space on the page.
        """
        provider = DisplayAdService.provider()
        base = {
            "enabled": False,
            "provider": PROVIDER_NONE,
            "client_id": "",
            "network_code": "",
            "units": {},
        }

        if not DisplayAdService.enabled() or provider == PROVIDER_NONE:
            return base

        client_id = (get_config_value("ADSENSE_CLIENT_ID", settings.ADSENSE_CLIENT_ID) or "").strip()
        network_code = (
            get_config_value("GAM_NETWORK_CODE", getattr(settings, "GAM_NETWORK_CODE", "")) or ""
        ).strip()
        units = DisplayAdService.units()

        account_id = client_id if provider == PROVIDER_ADSENSE else network_code
        if not account_id or not units:
            logger.info(
                "Display ads are on but provider %s is not fully configured; serving none.",
                provider,
            )
            return base

        return {
            "enabled": True,
            "provider": provider,
            "client_id": client_id if provider == PROVIDER_ADSENSE else "",
            "network_code": network_code if provider == PROVIDER_AD_MANAGER else "",
            "units": units,
        }
