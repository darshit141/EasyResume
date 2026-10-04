"""Authenticator-app two-factor, managed by the signed-in user from Settings.

Setup is two steps on purpose: ``setup`` stores an unconfirmed secret and
returns the QR code; nothing changes at sign-in until ``confirm`` receives a
working code from the app. That way a half-finished setup (the user closed
the tab before scanning) can never lock anyone out.
"""

from rest_framework import permissions, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from authentication.services import auth_security


class _CodeSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=20)


class _DisableSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=20)
    password = serializers.CharField(required=False, allow_blank=True, write_only=True)


def _status_payload(user, policy):
    return {
        # Whether this site offers authenticator apps at all (admin switch).
        "available": policy.totp_enabled,
        "totp_enabled": bool(user.totp_enabled and user.totp_secret),
        # On only while the site switch is on too: that's what sign-in obeys.
        "active": auth_security.totp_active(user, policy),
        "backup_codes_remaining": auth_security.backup_codes_remaining(user),
        "email_code_on_login": policy.email_otp_required,
        # Google-only accounts have no password to re-enter when turning 2FA off.
        "has_password": user.has_usable_password(),
    }


def _locked_response(user):
    """429 while the account is locked from too many wrong passwords/codes."""
    seconds = auth_security.lockout_seconds_remaining(user)
    if not seconds:
        return None
    return Response(auth_security.locked_out_payload(seconds), status=status.HTTP_429_TOO_MANY_REQUESTS)


def _wrong_attempt(user, body):
    """Count one wrong password or code. If that tips the account into a
    lockout, answer with the lockout instead of the plain 400."""
    seconds = auth_security.record_failed_login(user)
    if seconds:
        return Response(auth_security.locked_out_payload(seconds), status=status.HTTP_429_TOO_MANY_REQUESTS)
    return Response(body, status=status.HTTP_400_BAD_REQUEST)


class TwoFactorStatusAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        policy = auth_security.get_policy()
        return Response(_status_payload(request.user, policy))


class TwoFactorSetupAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "auth_sensitive"

    def post(self, request):
        policy = auth_security.get_policy()
        user = request.user
        if not policy.totp_enabled:
            return Response(
                {"message": "Authenticator apps aren't available right now.", "code": "TOTP_UNAVAILABLE"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if user.totp_enabled:
            return Response(
                {
                    "message": "An authenticator app is already set up. Turn it off first to add a different one.",
                    "code": "TOTP_ALREADY_ENABLED",
                },
                status=status.HTTP_409_CONFLICT,
            )
        return Response(auth_security.begin_totp_setup(user, policy), status=status.HTTP_200_OK)


class TwoFactorConfirmAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "auth_sensitive"

    def post(self, request):
        serializer = _CodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        policy = auth_security.get_policy()
        user = request.user
        if not policy.totp_enabled:
            return Response(
                {"message": "Authenticator apps aren't available right now.", "code": "TOTP_UNAVAILABLE"},
                status=status.HTTP_403_FORBIDDEN,
            )
        locked = _locked_response(user)
        if locked:
            return locked
        codes = auth_security.confirm_totp_setup(user, serializer.validated_data["code"])
        if codes is None:
            return _wrong_attempt(
                user,
                {
                    "message": "That code didn't match. Check the time on your phone is set automatically, then try the newest code.",
                    "code": "OTP_INVALID",
                },
            )
        payload = _status_payload(user, policy)
        payload["backup_codes"] = codes
        return Response(payload, status=status.HTTP_200_OK)


class TwoFactorDisableAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "auth_sensitive"

    def post(self, request):
        serializer = _DisableSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not user.totp_enabled:
            return Response(_status_payload(user, auth_security.get_policy()))

        # Someone at an unlocked laptop must not be able to strip 2FA: ask
        # for the password (when the account has one) and a current code.
        locked = _locked_response(user)
        if locked:
            return locked
        if user.has_usable_password() and not user.check_password(
            serializer.validated_data.get("password") or ""
        ):
            return _wrong_attempt(
                user, {"message": "Your password isn't right.", "code": "PASSWORD_INVALID"}
            )
        if auth_security.verify_second_factor_code(user, serializer.validated_data["code"]) is None:
            return _wrong_attempt(user, {"message": "That code isn't right.", "code": "OTP_INVALID"})
        auth_security.disable_totp(user)
        return Response(_status_payload(user, auth_security.get_policy()))


class TwoFactorBackupCodesAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]
    throttle_scope = "auth_sensitive"

    def post(self, request):
        serializer = _CodeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = request.user
        if not auth_security.totp_active(user):
            return Response(
                {"message": "Set up an authenticator app first.", "code": "TOTP_NOT_ENABLED"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        locked = _locked_response(user)
        if locked:
            return locked
        if auth_security.verify_second_factor_code(user, serializer.validated_data["code"]) != "totp":
            return _wrong_attempt(
                user,
                {"message": "Enter the current code from your authenticator app.", "code": "OTP_INVALID"},
            )
        payload = _status_payload(user, auth_security.get_policy())
        payload["backup_codes"] = auth_security.regenerate_backup_codes(user)
        payload["backup_codes_remaining"] = len(payload["backup_codes"])
        return Response(payload)
