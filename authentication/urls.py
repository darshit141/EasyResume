from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from authentication.views import (
    RegisterAPIView,
    LoginAPIView,
    ForgotPasswordAPIView,
    VerifyOTPAPIView,
    ResetPasswordAPIView,
    VerifyLoginOTPAPIView,
    ProfileAPIView,
    ChangePasswordAPIView,
    LogoutAPIView,
)

urlpatterns = [
    path(
        "register/",
        RegisterAPIView.as_view(),
        name="register",
    ),
    path(
        "login/",
        LoginAPIView.as_view(),
        name="login",
    ),
    path(
        "login/verify-otp/",
        VerifyLoginOTPAPIView.as_view(),
        name="login-verify-otp",
    ),
    path(
        "forgot-password/",
        ForgotPasswordAPIView.as_view(),
        name="forgot-password",
    ),
    path(
        "verify-otp/",
        VerifyOTPAPIView.as_view(),
        name="verify-otp",
    ),
    path(
        "reset-password/",
        ResetPasswordAPIView.as_view(),
        name="reset-password",
    ),
    path(
        "refresh/",
        TokenRefreshView.as_view(),
        name="token-refresh",
    ),
    path(
        "logout/",
        LogoutAPIView.as_view(),
        name="logout",
    ),
    path(
        "me/",
        ProfileAPIView.as_view(),
        name="profile",
    ),
    path(
        "change-password/",
        ChangePasswordAPIView.as_view(),
        name="change-password",
    ),
]