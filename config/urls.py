from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)

from blog.views import RobotsTxtView, SitemapXMLView

urlpatterns = [
    # Served from a private path in production (DJANGO_ADMIN_URL) so the
    # well-known /admin/ login page doesn't advertise what the backend runs on.
    path(settings.ADMIN_URL, admin.site.urls),

    # Search engines fetch these from the site root, not from /api/ — see
    # blog/views/seo_view.py for why. Production nginx proxies both paths
    # straight to this backend, the same way it already does for /api/.
    path("sitemap.xml", SitemapXMLView.as_view(), name="sitemap-xml"),
    path("robots.txt", RobotsTxtView.as_view(), name="robots-txt"),

    # Existing APIs
    path("api/", include("api.urls")),

    # Authentication APIs
    path("api/auth/", include("authentication.urls")),

    # Resume APIs
    path("api/resumes/", include("resumes.urls")),

    # Billing APIs
    path("api/billing/", include("billing.urls")),

    # Blog / marketing APIs
    path("api/blog/", include("blog.urls")),
    path("api/inbox/", include("inbox.urls")),

    # Background AI job status polling
    path("api/ai-jobs/", include("ai_jobs.urls")),
]

# The OpenAPI schema and Swagger UI map every endpoint, so they are a
# development aid only: off in production unless API_DOCS_ENABLED says so.
if settings.API_DOCS_ENABLED:
    urlpatterns += [
        path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
        path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    ]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )