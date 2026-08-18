import random
from datetime import timedelta

from django.utils import timezone

from authentication.models import PasswordResetOTP, User
from authentication.utils.otp_mailer import send_otp_email


class ForgotPasswordService:
    """
    Service for forgot password.
    """

    @staticmethod
    def generate_otp():
        """
        Generate a random 6-digit OTP.
        """
        return str(random.randint(100000, 999999))

    @staticmethod
    def send_otp(email):
        """
        Generate and save OTP for password reset.
        """

        user = User.objects.filter(email=email).first()

        if user is None:
            return None

        # Delete previous OTPs
        PasswordResetOTP.objects.filter(user=user).delete()

        otp = ForgotPasswordService.generate_otp()

        PasswordResetOTP.objects.create(
            user=user,
            otp=otp,
            expires_at=timezone.now() + timedelta(minutes=5),
        )

        send_otp_email(user.email, otp, purpose="password reset")

        return otp