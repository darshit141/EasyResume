from django.contrib.auth import authenticate
from django.utils import timezone
from datetime import timedelta
from random import randint

from rest_framework import status

from authentication.models import LoginOTP


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

        # Generate new 6-digit OTP
        otp = str(randint(100000, 999999))

        # Create new OTP
        LoginOTP.objects.create(
            user=user,
            otp=otp,
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        # For development/testing only
        print("=" * 60)
        print(f"Login OTP for {user.email}: {otp}")
        print("=" * 60)

        return {
            "success": True,
            "message": "OTP sent successfully.",
            "email": user.email,
            "status": status.HTTP_200_OK,
        }