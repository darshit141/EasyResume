from authentication.models import PasswordResetOTP, User
from authentication.services import auth_security


class VerifyOTPService:
    """
    Service for OTP verification.
    """

    RESTART_HINT = "Request a new code to try again."

    @staticmethod
    def verify_otp(email, otp):
        """
        Verify the password-reset OTP for the given user.
        """

        policy = auth_security.get_policy()
        user = User.objects.filter(email=email).first()

        # Same answer as a wrong code: this step must not reveal whether an
        # account exists any more than the forgot-password step does.
        if user is None:
            return {
                "success": False,
                "message": "That code isn't valid. Request a new one.",
            }

        password_reset_otp = (
            PasswordResetOTP.objects.filter(user=user)
            .order_by("-created_at")
            .first()
        )

        if password_reset_otp is None:
            return {
                "success": False,
                "message": "That code isn't valid. Request a new one.",
            }

        if password_reset_otp.is_expired():
            return {
                "success": False,
                "message": "That code has expired. Request a new one.",
            }

        if auth_security.attempts_exhausted(password_reset_otp, policy):
            password_reset_otp.delete()
            return VerifyOTPService._wrong(0)

        if not auth_security.codes_match(password_reset_otp.otp, otp):
            remaining = auth_security.record_wrong_code(password_reset_otp, policy)
            return VerifyOTPService._wrong(remaining)

        password_reset_otp.is_verified = True
        password_reset_otp.save()

        return {
            "success": True,
            "message": "OTP verified successfully.",
        }

    @staticmethod
    def _wrong(remaining):
        return {
            "success": False,
            "code": "OTP_ATTEMPTS_EXCEEDED" if remaining <= 0 else "OTP_INVALID",
            "attempts_remaining": remaining,
            "message": auth_security.wrong_code_message(remaining, VerifyOTPService.RESTART_HINT),
        }
