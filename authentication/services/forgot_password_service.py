from authentication.models import PasswordResetOTP, User
from authentication.services import auth_security
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
        return auth_security.generate_numeric_code()

    @staticmethod
    def send_otp(email):
        """
        Generate, save and email a password-reset code.

        Returns None when no account has this email. The view answers that
        case exactly like a sent code, so this endpoint can't be used to
        find out who has an account.
        """

        user = User.objects.filter(email=email).first()

        if user is None:
            return None

        policy = auth_security.get_policy()
        latest = PasswordResetOTP.objects.filter(user=user).first()
        wait = auth_security.resend_wait_seconds(latest, policy)
        if (
            latest is not None
            and wait > 0
            and not latest.is_verified
            and not auth_security.attempts_exhausted(latest, policy)
        ):
            # One went out moments ago and is still good: don't mail a new
            # one on every click (and don't reset its attempt count).
            return latest.otp

        # Delete previous OTPs
        PasswordResetOTP.objects.filter(user=user).delete()

        otp = ForgotPasswordService.generate_otp()

        PasswordResetOTP.objects.create(
            user=user,
            otp=otp,
            expires_at=auth_security.code_expiry(policy),
        )

        send_otp_email(
            user.email, otp, purpose="password reset", expiry_minutes=policy.otp_expiry_minutes
        )

        return otp
