from django.contrib.auth import authenticate
from rest_framework import status

from authentication.models import User
from authentication.services import auth_security


class LoginService:

    @staticmethod
    def login_user(email, password):
        policy = auth_security.get_policy()
        account = User.objects.filter(email=email).first()

        # A locked account refuses every password, right or wrong, so the
        # lock can't be used to keep testing guesses. Checked before the
        # password so a locked account costs no hashing either.
        locked_for = auth_security.lockout_seconds_remaining(account)
        if locked_for:
            return LoginService._locked(locked_for)

        user = authenticate(email=email, password=password)

        if user is None:
            if account is not None and account.is_active:
                locked_for = auth_security.record_failed_login(account, policy)
                if locked_for:
                    return LoginService._locked(locked_for)
            return {
                "success": False,
                "code": "INVALID_CREDENTIALS",
                "message": "Invalid email or password.",
                "status": status.HTTP_401_UNAUTHORIZED,
            }

        auth_security.clear_failed_logins(user)

        # Read through the runtime config (inside get_policy) rather than
        # settings directly, so an admin changing any of this in the admin
        # panel takes effect on the very next login instead of a redeploy.
        challenge = auth_security.start_second_factor(user, first_factor="password", policy=policy)

        if challenge is None:
            # Single-factor: the password alone is accepted.
            tokens = auth_security.issue_tokens(user)
            return {
                "success": True,
                "otp_required": False,
                "message": "Login successful.",
                "email": user.email,
                "access": tokens["access"],
                "refresh": tokens["refresh"],
                "user": user,
                "status": status.HTTP_200_OK,
            }

        return {
            "success": True,
            "otp_required": True,
            "otp_method": challenge["method"],
            "message": challenge["message"],
            "email": user.email,
            "resend_available_in": challenge.get("resend_available_in", 0),
            "expires_in_seconds": challenge.get("expires_in_seconds"),
            "status": status.HTTP_200_OK,
        }

    @staticmethod
    def _locked(seconds):
        return {
            "success": False,
            "code": "ACCOUNT_LOCKED",
            "message": auth_security.lockout_message(seconds),
            "retry_after_seconds": seconds,
            "status": status.HTTP_429_TOO_MANY_REQUESTS,
        }
