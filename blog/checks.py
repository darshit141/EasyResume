"""Deploy-time sanity check for the public site address.

sitemap.xml, robots.txt, Stripe return links and email links are all built from
FRONTEND_URL. If production still carries the localhost default or a retired
domain, search engines are told to crawl a dead address and the AdSense/Search
Console submissions fail without any error being raised. This surfaces it in
`manage.py check --deploy`, which the deploy job already runs.
"""
from django.conf import settings
from django.core.checks import Tags, Warning, register

RETIRED_HOSTS = ("patrixel.com",)


@register(Tags.security, deploy=True)
def frontend_url_is_public(app_configs, **kwargs):
    url = (getattr(settings, "FRONTEND_URL", "") or "").lower()
    if not url.startswith("https://"):
        return [
            Warning(
                f"FRONTEND_URL is {url!r}, not an https:// address.",
                hint="Set FRONTEND_URL=https://intelliresume.online in the server's .env. "
                "The sitemap, robots.txt, Stripe return links and email links are built from it.",
                id="blog.W001",
            )
        ]
    host = url.split("://", 1)[1].split("/", 1)[0]
    if any(host == h or host.endswith("." + h) for h in RETIRED_HOSTS):
        return [
            Warning(
                f"FRONTEND_URL points at a retired domain ({host}).",
                hint="Set FRONTEND_URL=https://intelliresume.online in the server's .env and restart.",
                id="blog.W002",
            )
        ]
    return []
