from django.urls import path
from .views.health import health_check
from .views.readiness import readiness_check
from .views.admin_views import (
    SystemConfigurationListUpdateAPIView,
    SystemConfigurationResetAPIView,
    AdminUserListAPIView,
    AdminUserToggleActiveAPIView,
    AdminUserToggleStaffAPIView,
    AdminStatsAPIView,
)
from .views.business_metrics_view import AdminBusinessMetricsAPIView
from .views.analytics_views import TrackEventAPIView
from .views.b2b_lead_views import B2BLeadCreateAPIView

urlpatterns = [
    path("health/", health_check, name="health-check"),
    path("ready/", readiness_check, name="readiness-check"),

    # Analytics
    path("analytics/track/", TrackEventAPIView.as_view(), name="analytics-track"),

    # Leads
    path("leads/b2b/", B2BLeadCreateAPIView.as_view(), name="b2b-lead-create"),

    # Admin endpoints
    path("admin/config/", SystemConfigurationListUpdateAPIView.as_view(), name="admin-config"),
    path("admin/config/reset/", SystemConfigurationResetAPIView.as_view(), name="admin-config-reset"),
    path("admin/users/", AdminUserListAPIView.as_view(), name="admin-users"),
    path("admin/stats/", AdminStatsAPIView.as_view(), name="admin-stats"),
    path("admin/business-metrics/", AdminBusinessMetricsAPIView.as_view(), name="admin-business-metrics"),
    path("admin/users/<int:user_id>/toggle-active/", AdminUserToggleActiveAPIView.as_view(), name="admin-user-toggle-active"),
    path("admin/users/<int:user_id>/toggle-staff/", AdminUserToggleStaffAPIView.as_view(), name="admin-user-toggle-staff"),
]
