from django.urls import path
from .views.health import health_check
from .views.readiness import readiness_check
from .views.admin_views import (
    SystemConfigurationListUpdateAPIView,
    SystemConfigurationResetAPIView,
    SystemConfigurationTestAIAPIView,
    AdminUserListAPIView,
    AdminUserToggleActiveAPIView,
    AdminUserToggleStaffAPIView,
    AdminUserSecurityResetAPIView,
    AdminStatsAPIView,
    AdminAuditLogListAPIView,
    AdminAIModelStatsAPIView,
)
from .views.business_metrics_view import AdminBusinessMetricsAPIView
from .views.analytics_views import TrackEventAPIView
from .views.b2b_lead_views import (
    AdminB2BLeadListAPIView,
    AdminB2BLeadToggleContactedAPIView,
    B2BLeadCreateAPIView,
)
from .views.feedback_views import FeedbackCreateAPIView
from .views.site_views import SiteConfigAPIView, SiteImageFileAPIView
from .views.site_image_admin_views import (
    AdminSiteImageDetailAPIView,
    AdminSiteImageListAPIView,
    AdminSiteImageUploadAPIView,
)

urlpatterns = [
    path("health/", health_check, name="health-check"),
    path("ready/", readiness_check, name="readiness-check"),

    # Analytics
    path("analytics/track/", TrackEventAPIView.as_view(), name="analytics-track"),

    # Feedback
    path("feedback/", FeedbackCreateAPIView.as_view(), name="feedback-create"),

    # Public: presentation settings for the homepage (no auth).
    path("site/config/", SiteConfigAPIView.as_view(), name="site-config"),
    path("site/images/<slug:slot>/", SiteImageFileAPIView.as_view(), name="site-image-file"),

    # Admin: site images, slot by slot.
    path("admin/site-images/", AdminSiteImageListAPIView.as_view(), name="admin-site-images"),
    path("admin/site-images/<slug:slot>/", AdminSiteImageDetailAPIView.as_view(), name="admin-site-image"),
    path(
        "admin/site-images/<slug:slot>/upload/",
        AdminSiteImageUploadAPIView.as_view(),
        name="admin-site-image-upload",
    ),

    # Leads
    path("leads/b2b/", B2BLeadCreateAPIView.as_view(), name="b2b-lead-create"),
    path("admin/leads/b2b/", AdminB2BLeadListAPIView.as_view(), name="admin-b2b-leads"),
    path(
        "admin/leads/b2b/<int:lead_id>/toggle-contacted/",
        AdminB2BLeadToggleContactedAPIView.as_view(),
        name="admin-b2b-lead-toggle-contacted",
    ),

    # Admin endpoints
    path("admin/config/", SystemConfigurationListUpdateAPIView.as_view(), name="admin-config"),
    path("admin/config/reset/", SystemConfigurationResetAPIView.as_view(), name="admin-config-reset"),
    path("admin/config/test-ai/", SystemConfigurationTestAIAPIView.as_view(), name="admin-config-test-ai"),
    path("admin/users/", AdminUserListAPIView.as_view(), name="admin-users"),
    path("admin/stats/", AdminStatsAPIView.as_view(), name="admin-stats"),
    path("admin/ai-model-stats/", AdminAIModelStatsAPIView.as_view(), name="admin-ai-model-stats"),
    path("admin/business-metrics/", AdminBusinessMetricsAPIView.as_view(), name="admin-business-metrics"),
    path("admin/users/<int:user_id>/toggle-active/", AdminUserToggleActiveAPIView.as_view(), name="admin-user-toggle-active"),
    path("admin/users/<int:user_id>/toggle-staff/", AdminUserToggleStaffAPIView.as_view(), name="admin-user-toggle-staff"),
    path("admin/users/<int:user_id>/security-reset/", AdminUserSecurityResetAPIView.as_view(), name="admin-user-security-reset"),
    path("admin/audit-log/", AdminAuditLogListAPIView.as_view(), name="admin-audit-log"),
]
