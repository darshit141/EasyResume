from django.utils import timezone

from authentication.models import PasswordResetOTP, User


class VerifyOTPService:
    """
    Service for OTP verification.
    """

    @staticmethod
    def verify_otp(email, otp):
        """
        Verify the OTP for the given user.
        """

        user = User.objects.filter(email=email).first()

        if user is None:
            return {
                "success": False,
                "message": "User with this email does not exist.",
            }

        password_reset_otp = (
            PasswordResetOTP.objects.filter(user=user)
            .order_by("-created_at")
            .first()
        )

        if password_reset_otp is None:
            return {
                "success": False,
                "message": "OTP not found.",
            }

        if password_reset_otp.is_expired():
            return {
                "success": False,
                "message": "OTP has expired.",
            }

        if password_reset_otp.otp != otp:
            return {
                "success": False,
                "message": "Invalid OTP.",
            }

        password_reset_otp.is_verified = True
        password_reset_otp.save()

        return {
            "success": True,
            "message": "OTP verified successfully.",
        }