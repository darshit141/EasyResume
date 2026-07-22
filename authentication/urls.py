from django.urls import path

from authentication.views import (
    RegisterAPIView,
    LoginAPIView,
    ForgotPasswordAPIView,
    VerifyOTPAPIView,
    ResetPasswordAPIView,
    VerifyLoginOTPAPIView,
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
]