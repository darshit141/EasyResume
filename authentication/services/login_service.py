from django.conf import settings
from django.contrib.auth import authenticate
from django.utils import timezone
from datetime import timedelta
from random import randint

from rest_framework import status

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
            "message": "OTP sent successfully.",
            "email": user.email,
            "status": status.HTTP_200_OK,
        }