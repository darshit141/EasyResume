"""Sign-in hardening shared by every authentication path.

- account lockout after repeated wrong passwords,
- attempt limits, expiry and a resend cooldown on one-time codes,
- authenticator-app two-factor (TOTP) with single-use backup codes.

Every limit is read through get_config_value at call time, so the admin
panel's Security settings take effect on the very next request. The values
in settings.py are only the fallback when nothing has been saved.
"""

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.models import LoginOTP, TwoFactorBackupCode, User
from authentication.utils import totp

logger = logging.getLogger(__name__)

BACKUP_CODE_COUNT = 10
_BACKUP_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"  # no 0/o/1/l/i


# --- policy -----------------------------------------------------------------


@dataclass(frozen=True)
class SecurityPolicy:
    email_otp_required: bool
    max_failed_logins: int
    lockout_minutes: int
    otp_max_attempts: int
    otp_expiry_minutes: int
    otp_resend_cooldown_seconds: int
    totp_enabled: bool
    totp_issuer: str


def _int_setting(key, minimum, maximum):
    from api.utils.config import get_config_value

    raw = get_config_value(key, getattr(settings, key))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = int(getattr(settings, key))
    # Clamp rather than trust: a typo in the admin panel (say an expiry of
    # 0 minutes, or 100000 allowed guesses) must not quietly disable a
    # protection or make login impossible.
    return max(minimum, min(maximum, value))


def _bool_setting(key):
    from api.utils.config import get_config_value

    value = get_config_value(key, getattr(settings, key))
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "1", "t", "y")
    return bool(value)


def get_policy():
    from api.utils.config import get_config_value

    return SecurityPolicy(
        email_otp_required=_bool_setting("LOGIN_OTP_REQUIRED"),
        # 0 switches the lockout off entirely.
        max_failed_logins=_int_setting("LOGIN_MAX_FAILED_ATTEMPTS", 0, 100),
        lockout_minutes=_int_setting("LOGIN_LOCKOUT_MINUTES", 1, 24 * 60),
        otp_max_attempts=_int_setting("OTP_MAX_ATTEMPTS", 1, 20),
        otp_expiry_minutes=_int_setting("OTP_EXPIRY_MINUTES", 1, 60),
        otp_resend_cooldown_seconds=_int_setting("OTP_RESEND_COOLDOWN_SECONDS", 0, 3600),
        totp_enabled=_bool_setting("TOTP_2FA_ENABLED"),
        totp_issuer=(
            str(get_config_value("TOTP_ISSUER_NAME", settings.TOTP_ISSUER_NAME) or "").strip()
            or settings.TOTP_ISSUER_NAME
        ),
    )


# --- account lockout ----------------------------------------------------------


def lockout_seconds_remaining(user):
    if user is None or user.locked_until is None:
        return 0
    remaining = (user.locked_until - timezone.now()).total_seconds()
    return max(0, int(remaining + 0.999))


def record_failed_login(user, policy=None):
    """Count one wrong password. Returns the seconds the account is now
    locked for (0 while it isn't)."""
    policy = policy or get_policy()
    if policy.max_failed_logins <= 0:
        return 0

    # F() so two wrong passwords arriving together both count.
    User.objects.filter(pk=user.pk).update(failed_login_attempts=F("failed_login_attempts") + 1)
    user.refresh_from_db(fields=["failed_login_attempts", "locked_until"])

    if user.failed_login_attempts < policy.max_failed_logins:
        return 0

    user.locked_until = timezone.now() + timedelta(minutes=policy.lockout_minutes)
    user.failed_login_attempts = 0
    user.save(update_fields=["locked_until", "failed_login_attempts"])
    logger.warning(
        "Account locked after repeated failed sign-ins user_id=%s minutes=%s",
        user.pk, policy.lockout_minutes,
    )
    return lockout_seconds_remaining(user)


def clear_failed_logins(user):
    if user.failed_login_attempts or user.locked_until:
        user.failed_login_attempts = 0
        user.locked_until = None
        user.save(update_fields=["failed_login_attempts", "locked_until"])


def lockout_message(seconds):
    minutes = max(1, (seconds + 59) // 60)
    unit = "minute" if minutes == 1 else "minutes"
    return (
        f"Too many failed sign-in attempts. Try again in {minutes} {unit}, "
        "or reset your password to get back in now."
    )


# --- one-time codes -------------------------------------------------------------


def generate_numeric_code():
    # A fixed code in local development so testers don't need the server
    # log to sign in; production is always random (secrets, not random).
    if settings.DEBUG:
        return "123456"
    return f"{secrets.randbelow(1_000_000):06d}"


def code_expiry(policy=None):
    policy = policy or get_policy()
    return timezone.now() + timedelta(minutes=policy.otp_expiry_minutes)


def resend_wait_seconds(latest, policy=None):
    """Seconds before another code may be sent, given the newest one."""
    policy = policy or get_policy()
    if latest is None or latest.is_expired():
        return 0
    elapsed = (timezone.now() - latest.created_at).total_seconds()
    return max(0, int(policy.otp_resend_cooldown_seconds - elapsed + 0.999))


def codes_match(expected, given):
    return bool(expected) and hmac.compare_digest(str(expected), str(given or "").strip())


def record_wrong_code(code_row, policy=None):
    """Count one wrong guess against ``code_row`` (a LoginOTP or a
    PasswordResetOTP). Returns how many guesses are left; at 0 the code has
    been deleted and a new one has to be requested."""
    policy = policy or get_policy()
    model = type(code_row)
    # Only rows still under the limit are counted, so concurrent guesses
    # can't slip past it between a read and a write.
    counted = model.objects.filter(
        pk=code_row.pk, attempts__lt=policy.otp_max_attempts
    ).update(attempts=F("attempts") + 1)
    attempts = (
        model.objects.filter(pk=code_row.pk).values_list("attempts", flat=True).first()
    )
    if not counted or attempts is None or attempts >= policy.otp_max_attempts:
        model.objects.filter(pk=code_row.pk).delete()
        return 0
    return policy.otp_max_attempts - attempts


def attempts_exhausted(code_row, policy=None):
    policy = policy or get_policy()
    return code_row.attempts >= policy.otp_max_attempts


def wrong_code_message(remaining, restart_hint):
    if remaining <= 0:
        return f"Too many incorrect codes. {restart_hint}"
    unit = "attempt" if remaining == 1 else "attempts"
    return f"That code isn't right. {remaining} {unit} left."


# --- authenticator app (TOTP) ------------------------------------------------------


def _encrypt(secret):
    from api.utils.secrets import DOMAIN_TOTP_SECRET, encrypt_value

    return encrypt_value(secret, domain=DOMAIN_TOTP_SECRET)


def user_totp_secret(user):
    from api.utils.secrets import DOMAIN_TOTP_SECRET, SecretDecryptionError, decrypt_value

    if not user.totp_secret:
        return ""
    try:
        return decrypt_value(user.totp_secret, domain=DOMAIN_TOTP_SECRET)
    except SecretDecryptionError:
        # The encryption key changed. Failing closed would lock the user out
        # for good, so this is loud in the logs and support resets it.
        logger.error("TOTP secret for user_id=%s could not be decrypted", user.pk)
        return ""


def totp_active(user, policy=None):
    policy = policy or get_policy()
    return bool(policy.totp_enabled and user.totp_enabled and user.totp_secret)


def begin_totp_setup(user, policy=None):
    """Store a fresh, unconfirmed secret and return what the setup screen
    shows. Nothing changes at sign-in until confirm_totp_setup succeeds."""
    policy = policy or get_policy()
    secret = totp.generate_secret()
    user.totp_secret = _encrypt(secret)
    user.totp_enabled = False
    user.totp_last_step = None
    user.save(update_fields=["totp_secret", "totp_enabled", "totp_last_step"])
    uri = totp.provisioning_uri(secret, user.email, policy.totp_issuer)
    return {
        "secret": secret,
        "otpauth_uri": uri,
        "qr_svg": totp.qr_svg_data_uri(uri),
    }


def _accept_totp_code(user, code):
    secret = user_totp_secret(user)
    if not secret:
        return False
    step = totp.matching_step(secret, code)
    if step is None:
        return False
    # Replay guard: a code is only good once, even inside its 30 seconds.
    # The conditional update makes two simultaneous uses resolve to one.
    from django.db.models import Q

    updated = User.objects.filter(pk=user.pk).filter(
        Q(totp_last_step__isnull=True) | Q(totp_last_step__lt=step)
    ).update(totp_last_step=step)
    if not updated:
        return False
    user.totp_last_step = step
    return True


def confirm_totp_setup(user, code):
    """Turn the authenticator on once the user proves the app works.
    Returns the plaintext backup codes (shown once), or None on a bad code."""
    if not user.totp_secret or user.totp_enabled:
        return None
    if not _accept_totp_code(user, code):
        return None
    user.totp_enabled = True
    user.save(update_fields=["totp_enabled"])
    LoginOTP.objects.filter(user=user).delete()
    return regenerate_backup_codes(user)


def disable_totp(user):
    user.totp_secret = ""
    user.totp_enabled = False
    user.totp_last_step = None
    user.save(update_fields=["totp_secret", "totp_enabled", "totp_last_step"])
    TwoFactorBackupCode.objects.filter(user=user).delete()
    LoginOTP.objects.filter(user=user, method=LoginOTP.METHOD_TOTP).delete()


def _hash_backup_code(code):
    normalized = "".join(ch for ch in (code or "").lower() if ch.isalnum())
    key = f"intelliresume:backup-code:{settings.SECRET_KEY}".encode("utf-8")
    return hmac.new(key, normalized.encode("utf-8"), hashlib.sha256).hexdigest()


def _new_backup_code():
    raw = "".join(secrets.choice(_BACKUP_ALPHABET) for _ in range(10))
    return f"{raw[:5]}-{raw[5:]}"


def regenerate_backup_codes(user):
    codes = [_new_backup_code() for _ in range(BACKUP_CODE_COUNT)]
    with transaction.atomic():
        TwoFactorBackupCode.objects.filter(user=user).delete()
        TwoFactorBackupCode.objects.bulk_create(
            TwoFactorBackupCode(user=user, code_hash=_hash_backup_code(code)) for code in codes
        )
    return codes


def backup_codes_remaining(user):
    return TwoFactorBackupCode.objects.filter(user=user, used_at__isnull=True).count()


def _use_backup_code(user, code):
    # Single-use: the update only succeeds for a code not yet spent.
    return bool(
        TwoFactorBackupCode.objects.filter(
            user=user, code_hash=_hash_backup_code(code), used_at__isnull=True
        ).update(used_at=timezone.now())
    )


def verify_second_factor_code(user, code):
    """An authenticator code or an unused backup code. Returns "totp",
    "backup" or None."""
    cleaned = (code or "").strip()
    if cleaned.replace(" ", "").isdigit():
        return "totp" if _accept_totp_code(user, cleaned) else None
    return "backup" if _use_backup_code(user, cleaned) else None


# --- the sign-in handshake -------------------------------------------------------


def issue_tokens(user):
    clear_failed_logins(user)
    refresh = RefreshToken.for_user(user)
    user.last_login = timezone.now()
    user.save(update_fields=["last_login"])
    return {"access": str(refresh.access_token), "refresh": str(refresh)}


def start_second_factor(user, *, first_factor="password", policy=None):
    """Called once the first factor has passed. Returns None when no second
    factor applies (tokens can be issued now), otherwise a dict describing
    the challenge the client must answer at auth/login/verify-otp/.

    The authenticator app, when the user has one, wins over the emailed
    code: it is the stronger factor and it doesn't depend on the mailbox a
    password reset also goes to. Google sign-in only ever asks for the
    authenticator - Google has already proved the mailbox, so emailing a
    code to it adds nothing.
    """
    policy = policy or get_policy()

    if totp_active(user, policy):
        LoginOTP.objects.filter(user=user).delete()
        LoginOTP.objects.create(
            user=user,
            otp="",
            method=LoginOTP.METHOD_TOTP,
            expires_at=code_expiry(policy),
        )
        return {
            "method": LoginOTP.METHOD_TOTP,
            "message": "Enter the 6-digit code from your authenticator app.",
            "expires_in_seconds": policy.otp_expiry_minutes * 60,
        }

    if first_factor != "password" or not policy.email_otp_required:
        # Any code left over from an abandoned sign-in must not stay
        # replayable once a session exists.
        LoginOTP.objects.filter(user=user).delete()
        return None

    latest = LoginOTP.objects.filter(user=user, method=LoginOTP.METHOD_EMAIL).first()
    wait = resend_wait_seconds(latest, policy)
    if latest is not None and wait > 0 and not attempts_exhausted(latest, policy):
        # A code went out moments ago and is still good: keep it (and its
        # attempt count) rather than mailing a new one on every click.
        return {
            "method": LoginOTP.METHOD_EMAIL,
            "message": "We've already sent you a code. Check your email.",
            "resend_available_in": wait,
            "expires_in_seconds": max(0, int((latest.expires_at - timezone.now()).total_seconds())),
        }

    from authentication.utils.otp_mailer import send_otp_email

    LoginOTP.objects.filter(user=user).delete()
    code = generate_numeric_code()
    LoginOTP.objects.create(
        user=user,
        otp=code,
        method=LoginOTP.METHOD_EMAIL,
        expires_at=code_expiry(policy),
    )
    send_otp_email(user.email, code, purpose="login", expiry_minutes=policy.otp_expiry_minutes)
    return {
        "method": LoginOTP.METHOD_EMAIL,
        "message": "OTP sent successfully.",
        "resend_available_in": policy.otp_resend_cooldown_seconds,
        "expires_in_seconds": policy.otp_expiry_minutes * 60,
    }


def user_payload(user):
    return {
        "id": user.id,
        "name": user.name,
        "email": user.email,
        "phone": user.phone,
        "is_staff": user.is_staff,
    }
