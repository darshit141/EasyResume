from django.contrib import admin
from django.urls import include, path
from django.conf import settings
from django.conf.urls.static import static

from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
)

urlpatterns = [
    path("admin/", admin.site.urls),

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

    # Background AI job status polling
    path("api/ai-jobs/", include("ai_jobs.urls")),

    # OpenAPI Schema
    path(
        "api/schema/",
        SpectacularAPIView.as_view(),
        name="schema",
    ),

    # Swagger UI
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
]

if settings.DEBUG:
    urlpatterns += static(
        settings.MEDIA_URL,
        document_root=settings.MEDIA_ROOT,
    )