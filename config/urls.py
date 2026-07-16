from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),

    # Existing APIs
    path("api/", include("api.urls")),

    # Authentication APIs
    path("api/auth/", include("authentication.urls")),
]