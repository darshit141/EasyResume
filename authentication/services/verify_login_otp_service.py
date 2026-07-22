from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.models import LoginOTP, User


class VerifyLoginOTPService:
    """
    Service for verifying login OTP.
    """

    @staticmethod
    def verify_otp(email, otp):
        """
        Verify login OTP and generate JWT tokens.
        """

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return {
                "success": False,
                "message": "User not found.",
                "status": status.HTTP_404_NOT_FOUND,
            }

        login_otp = (
            LoginOTP.objects.filter(user=user)
            .order_by("-created_at")
            .first()
        )

        if login_otp is None:
            return {
                "success": False,
                "message": "OTP not found.",
                "status": status.HTTP_404_NOT_FOUND,
            }

        if login_otp.is_expired():
            login_otp.delete()
            return {
                "success": False,
                "message": "OTP has expired.",
                "status": status.HTTP_401_UNAUTHORIZED,
            }

        if login_otp.otp != otp:
            return {
                "success": False,
                "message": "Invalid OTP.",
                "status": status.HTTP_401_UNAUTHORIZED,
            }

        # Generate JWT tokens
        refresh = RefreshToken.for_user(user)

        # Delete OTP after successful verification
        login_otp.delete()

        return {
            "success": True,
            "message": "Login successful.",
            "access": str(refresh.access_token),
            "refresh": str(refresh),
            "user": user,
        }