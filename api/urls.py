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

urlpatterns = [
    path("health/", health_check, name="health-check"),
    path("ready/", readiness_check, name="readiness-check"),
    
    # Admin endpoints
    path("admin/config/", SystemConfigurationListUpdateAPIView.as_view(), name="admin-config"),
    path("admin/config/reset/", SystemConfigurationResetAPIView.as_view(), name="admin-config-reset"),
    path("admin/users/", AdminUserListAPIView.as_view(), name="admin-users"),
    path("admin/stats/", AdminStatsAPIView.as_view(), name="admin-stats"),
    path("admin/users/<int:user_id>/toggle-active/", AdminUserToggleActiveAPIView.as_view(), name="admin-user-toggle-active"),
    path("admin/users/<int:user_id>/toggle-staff/", AdminUserToggleStaffAPIView.as_view(), name="admin-user-toggle-staff"),
]
