"""sitemap.xml and robots.txt — served at the site root, not under /api/.

The frontend is a React SPA (Vite) with no server of its own; the only
process that can produce these dynamically is this backend. Production nginx
(see intelliresume-react/nginx.conf) proxies /api/ to this service already;
the same proxy pattern extends to `/sitemap.xml` and `/robots.txt` so search
engines fetch both from the site's real, single public origin
(FRONTEND_URL) rather than from the API host, which is what both files are
required to be relative to.

Deliberately plain Django views, not django.contrib.sitemaps: that framework
is built around Django template views having their own reversible URLs,
which doesn't fit a separate SPA frontend whose routes this backend doesn't
own. A handful of curated marketing routes plus the published blog posts is
simple enough not to need it.
"""

from django.conf import settings
from django.http import HttpResponse
from django.views import View

from blog.models import Post

# Marketing/content pages worth indexing. Deliberately excludes functional
# routes (login, signup's OTP/reset steps, the outcome-tracking link) that
# are dead ends for a crawler and reveal internal flow structure for no SEO
# benefit. Kept here rather than generated from the frontend's router since
# this backend has no access to that router and a search engine doesn't
# care about routes it can't usefully land on anyway.
_STATIC_PAGES = (
    ("/", "1.0", "weekly"),
    ("/how-it-works", "0.8", "monthly"),
    ("/features", "0.8", "monthly"),
    ("/pricing", "0.8", "weekly"),
    ("/faq", "0.6", "monthly"),
    ("/about", "0.6", "monthly"),
    ("/contact", "0.4", "yearly"),
    ("/for-career-coaches", "0.5", "monthly"),
    ("/blog", "0.7", "daily"),
    ("/signup", "0.6", "monthly"),
    ("/privacy", "0.2", "yearly"),
    ("/terms", "0.2", "yearly"),
)


class SitemapXMLView(View):
    def get(self, request):
        base = settings.FRONTEND_URL.rstrip("/")
        entries = [
            f"<url><loc>{base}{path}</loc><changefreq>{freq}</changefreq><priority>{priority}</priority></url>"
            for path, priority, freq in _STATIC_PAGES
        ]
        for post in Post.published.all():
            entries.append(
                f"<url><loc>{base}/blog/{post.slug}</loc>"
                f"<lastmod>{post.updated_at.date().isoformat()}</lastmod>"
                f"<changefreq>monthly</changefreq><priority>0.6</priority></url>"
            )

        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            + "".join(entries)
            + "</urlset>"
        )
        return HttpResponse(xml, content_type="application/xml")


class RobotsTxtView(View):
    def get(self, request):
        base = settings.FRONTEND_URL.rstrip("/")
        if settings.DEBUG:
            # A non-production deploy (local, a review app) is never meant
            # to be indexed. Blocking everything here is the safe default
            # rather than trusting every environment to remember to set
            # DEBUG correctly before going live — the reverse mistake
            # (accidentally blocking the real site) is the one that costs
            # search ranking, so this only ever narrows, never widens, past
            # what an explicit production DEBUG=False setting allows.
            body = "User-agent: *\nDisallow: /\n"
        else:
            body = (
                "User-agent: *\n"
                "Allow: /\n"
                # Never meant for a search result: an authenticated app
                # shell, not content, and every route under it 401s for a
                # crawler anyway.
                "Disallow: /app/\n"
                # A confirmation page with nothing to find on its own.
                "Disallow: /thank-you\n"
                f"\nSitemap: {base}/sitemap.xml\n"
            )
        return HttpResponse(body, content_type="text/plain")
