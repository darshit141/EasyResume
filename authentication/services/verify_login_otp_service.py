from rest_framework import status

from authentication.models import LoginOTP, User
from authentication.services import auth_security


class VerifyLoginOTPService:
    """
    Service for verifying the second factor of a login: the emailed code,
    or - for users with an authenticator app - its code or a backup code.
    """

    RESTART_HINT = "Sign in again to get a new code."

    @staticmethod
    def verify_otp(email, otp):
        """
        Verify the pending challenge and generate JWT tokens.
        """

        policy = auth_security.get_policy()
        user = User.objects.filter(email=email).first()

        if user is None:
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
                "message": "This sign-in step has ended. Sign in again to get a new code.",
                "status": status.HTTP_404_NOT_FOUND,
            }

        if login_otp.is_expired():
            login_otp.delete()
            return {
                "success": False,
                "message": "This sign-in step has expired. Sign in again to continue.",
                "status": status.HTTP_401_UNAUTHORIZED,
            }

        if auth_security.attempts_exhausted(login_otp, policy):
            login_otp.delete()
            return VerifyLoginOTPService._wrong(0)

        if login_otp.method == LoginOTP.METHOD_TOTP:
            accepted = auth_security.verify_second_factor_code(user, otp) is not None
        else:
            accepted = auth_security.codes_match(login_otp.otp, otp)

        if not accepted:
            remaining = auth_security.record_wrong_code(login_otp, policy)
            return VerifyLoginOTPService._wrong(remaining)

        # Delete the challenge before issuing tokens, so it can't be reused.
        login_otp.delete()
        tokens = auth_security.issue_tokens(user)

        return {
            "success": True,
            "message": "Login successful.",
            "access": tokens["access"],
            "refresh": tokens["refresh"],
            "user": user,
        }

    @staticmethod
    def _wrong(remaining):
        return {
            "success": False,
            "code": "OTP_ATTEMPTS_EXCEEDED" if remaining <= 0 else "OTP_INVALID",
            "message": auth_security.wrong_code_message(
                remaining, VerifyLoginOTPService.RESTART_HINT
            ),
            "attempts_remaining": remaining,
            "status": (
                status.HTTP_429_TOO_MANY_REQUESTS if remaining <= 0 else status.HTTP_401_UNAUTHORIZED
            ),
        }
