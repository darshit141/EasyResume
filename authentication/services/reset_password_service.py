from authentication.models import PasswordResetOTP, User


class ResetPasswordService:
    """
    Service for resetting user password.
    """

    @staticmethod
    def reset_password(email, password):
        """
        Reset user password after successful OTP verification.
        """

        user = User.objects.filter(email=email).first()

        if user is None:
            return {
                "success": False,
                "message": "User with this email does not exist.",
            }

        password_reset_otp = (
            PasswordResetOTP.objects.filter(
                user=user,
                is_verified=True,
            )
            .order_by("-created_at")
            .first()
        )

        if password_reset_otp is None:
            return {
                "success": False,
                "message": "OTP verification required.",
            }

        if password_reset_otp.is_expired():
            return {
                "success": False,
                "message": "OTP has expired.",
            }

        user.set_password(password)
        user.save()

        # Delete OTP after successful password reset
        password_reset_otp.delete()

        return {
            "success": True,
            "message": "Password reset successfully.",
        }