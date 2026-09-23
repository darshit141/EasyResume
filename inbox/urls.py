from django.urls import path

from inbox.views import (
    AdminInboundEmailStatsAPIView,
    ApplyStatusAPIView,
    AssignInboundEmailAPIView,
    InboundEmailDetailAPIView,
    InboundEmailListAPIView,
    InboundEmailWebhookView,
    RotateTrackingAddressAPIView,
    TrackingAddressAPIView,
    UndoStatusAPIView,
)

urlpatterns = [
    path("inbound/", InboundEmailWebhookView.as_view(), name="inbox-inbound-webhook"),
    path("address/", TrackingAddressAPIView.as_view(), name="inbox-address"),
    path("address/rotate/", RotateTrackingAddressAPIView.as_view(), name="inbox-address-rotate"),
    path("emails/", InboundEmailListAPIView.as_view(), name="inbox-emails"),
    path("emails/<int:email_id>/", InboundEmailDetailAPIView.as_view(), name="inbox-email-detail"),
    path("emails/<int:email_id>/assign/", AssignInboundEmailAPIView.as_view(), name="inbox-email-assign"),
    path("emails/<int:email_id>/apply-status/", ApplyStatusAPIView.as_view(), name="inbox-email-apply-status"),
    path("emails/<int:email_id>/undo-status/", UndoStatusAPIView.as_view(), name="inbox-email-undo-status"),
    path("admin/stats/", AdminInboundEmailStatsAPIView.as_view(), name="inbox-admin-stats"),
]
