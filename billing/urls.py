from django.urls import path

from billing.views import (
    AdminCouponListCreateAPIView,
    AdminCouponToggleActiveAPIView,
    CouponRedeemAPIView,
    CreateCheckoutSessionAPIView,
    CreatePortalSessionAPIView,
    CreditsStatusAPIView,
    DisplayAdConfigAPIView,
    EntitlementsAPIView,
    PlanListAPIView,
    RewardedAdCallbackAPIView,
    RewardedAdTicketAPIView,
    StripeWebhookAPIView,
    SubscriptionStatusAPIView,
)

urlpatterns = [
    path("plans/", PlanListAPIView.as_view(), name="billing-plans"),
    path("subscription/", SubscriptionStatusAPIView.as_view(), name="billing-subscription"),
    path("entitlements/", EntitlementsAPIView.as_view(), name="billing-entitlements"),
    path("credits/", CreditsStatusAPIView.as_view(), name="billing-credits"),
    path("ads/config/", DisplayAdConfigAPIView.as_view(), name="billing-display-ads-config"),
    path(
        "credits/rewarded-ad/ticket/",
        RewardedAdTicketAPIView.as_view(),
        name="billing-rewarded-ad-ticket",
    ),
    path(
        "credits/rewarded-ad/callback/",
        RewardedAdCallbackAPIView.as_view(),
        name="billing-rewarded-ad-callback",
    ),
    path("checkout/", CreateCheckoutSessionAPIView.as_view(), name="billing-checkout"),
    path("portal/", CreatePortalSessionAPIView.as_view(), name="billing-portal"),
    path("webhook/", StripeWebhookAPIView.as_view(), name="billing-webhook"),
    path("coupons/redeem/", CouponRedeemAPIView.as_view(), name="billing-coupon-redeem"),
    path("admin/coupons/", AdminCouponListCreateAPIView.as_view(), name="billing-admin-coupons"),
    path(
        "admin/coupons/<int:coupon_id>/toggle-active/",
        AdminCouponToggleActiveAPIView.as_view(),
        name="billing-admin-coupon-toggle-active",
    ),
]
