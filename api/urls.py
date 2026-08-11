from django.urls import path
from .views.health import health_check
from .views.readiness import readiness_check

urlpatterns = [
    path("health/", health_check, name="health-check"),
    path("ready/", readiness_check, name="readiness-check"),
]
