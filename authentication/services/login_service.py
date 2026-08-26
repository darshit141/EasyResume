from django.conf import settings
from django.contrib.auth import authenticate
from django.utils import timezone
from datetime import timedelta
from random import randint

from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.models import LoginOTP
from authentication.utils.otp_mailer import send_otp_email


class LoginService:

    @staticmethod
    def login_user(email, password):
        user = authenticate(email=email, password=password)

        if user is None:
            return {
                "success": False,
                "message": "Invalid email or password.",
                "status": status.HTTP_401_UNAUTHORIZED,
            }

        # Read through the runtime config rather than settings directly, so
        # an admin toggling this in the admin panel takes effect on the very
        # next login instead of needing a redeploy.
        from api.utils.config import get_config_value

        otp_required = get_config_value("LOGIN_OTP_REQUIRED", settings.LOGIN_OTP_REQUIRED)

        if not otp_required:
            # Single-factor: the password alone is accepted, so issue tokens
            # straight away. Any OTP left over from a previous two-factor
            # login is cleared so it can't be replayed against the
            # verify-otp endpoint later.
            LoginOTP.objects.filter(user=user).delete()
            refresh = RefreshToken.for_user(user)
            return {
                "success": True,
                "otp_required": False,
                "message": "Login successful.",
                "email": user.email,
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": user,
                "status": status.HTTP_200_OK,
            }

        # Delete any previous unused OTPs
        LoginOTP.objects.filter(user=user).delete()

        # Generate new 6-digit OTP. In local development (DEBUG=True) this is
        # pinned to a fixed value so testers don't need console/log access to
        # complete login; production always uses a random OTP.
        otp = "123456" if settings.DEBUG else str(randint(100000, 999999))

        # Create new OTP
        LoginOTP.objects.create(
            user=user,
            otp=otp,
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        send_otp_email(user.email, otp, purpose="login")

        return {
            "success": True,
            "otp_required": True,
            "message": "OTP sent successfully.",
            "email": user.email,
            "status": status.HTTP_200_OK,
        }
