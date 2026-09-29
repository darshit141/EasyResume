from django.db import models
from django.contrib.auth.models import AbstractBaseUser, PermissionsMixin
from django.utils import timezone

from .managers import UserManager


class User(AbstractBaseUser, PermissionsMixin):
    """
    Custom User model for IntelliResume.
    """

    name = models.CharField(
        max_length=255,
    )

    email = models.EmailField(
        unique=True,
    )

    phone = models.CharField(
        max_length=30,
        unique=True,
        null=True,
        blank=True,
    )

    # Google's `sub` claim: the stable, immutable identifier for a Google
    # account. Deliberately the key we match on rather than the email —
    # a Google user can change the email on their account, and matching on
    # email alone would then either lock them out or, worse, hand their
    # IntelliResume account to whoever inherits the old address.
    #
    # Nullable because password accounts have no Google identity, and unique
    # so one Google account can never be linked to two users.
    google_sub = models.CharField(
        max_length=255,
        unique=True,
        null=True,
        blank=True,
        db_index=True,
    )

    is_active = models.BooleanField(
        default=True,
    )

    # The user's default resume layout template — a preset id from
    # resumes.resume_template.TEMPLATE_PRESETS plus an optional dict of
    # per-field overrides (Pro only). Every tailored resume the user
    # generates snapshots the resolved config onto its version, so changing
    # this never retroactively alters an already-generated resume.
    resume_template_id = models.CharField(
        max_length=40,
        default="ats_classic",
        blank=True,
    )

    resume_template_overrides = models.JSONField(
        default=dict,
        blank=True,
    )

    is_staff = models.BooleanField(
        default=False,
    )

    # Brute-force lockout (see settings.LOGIN_MAX_FAILED_ATTEMPTS). The
    # counter is consecutive wrong passwords; any successful sign-in or a
    # password reset puts it back to zero.
    failed_login_attempts = models.PositiveIntegerField(
        default=0,
    )

    locked_until = models.DateTimeField(
        null=True,
        blank=True,
    )

    # Authenticator-app two-factor. The shared secret is stored encrypted
    # (api.utils.secrets, its own key domain): a database dump must not be
    # enough to generate anyone's codes. A secret with totp_enabled False is
    # a setup the user started but never confirmed with a code.
    totp_secret = models.CharField(
        max_length=255,
        blank=True,
        default="",
    )

    totp_enabled = models.BooleanField(
        default=False,
    )

    # The 30-second time step of the last accepted code, so the same code
    # can't be replayed within its validity window.
    totp_last_step = models.BigIntegerField(
        null=True,
        blank=True,
    )

    date_joined = models.DateTimeField(
        default=timezone.now,
    )

    objects = UserManager()

    USERNAME_FIELD = "email"

    REQUIRED_FIELDS = ["name"]

    def __str__(self):
        return self.email





class PasswordResetOTP(models.Model):
    """
    Model to store password reset OTPs.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="password_reset_otps",
    )

    otp = models.CharField(
        max_length=6,
    )

    is_verified = models.BooleanField(
        default=False,
    )

    # Wrong guesses against this code. At settings.OTP_MAX_ATTEMPTS the code
    # is deleted and the user has to request a new one.
    attempts = models.PositiveSmallIntegerField(
        default=0,
    )

    expires_at = models.DateTimeField()

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-created_at"]

    def is_expired(self):
        return timezone.now() > self.expires_at

    def __str__(self):
        return f"{self.user.email} - {self.otp}"

class PolicyAcceptance(models.Model):
    """One row per time a user accepted a specific version of a legal
    policy. Append-only — a new acceptance is always a new row, never an
    update to a previous one, so the history of what a user agreed to and
    when is never silently overwritten (COMMERCIAL_READINESS_AUDIT.md P0-10).
    """

    POLICY_PRIVACY = "privacy_policy"
    POLICY_TERMS = "terms_of_service"
    POLICY_COOKIE = "cookie_policy"
    POLICY_TYPE_CHOICES = [
        (POLICY_PRIVACY, "Privacy Policy"),
        (POLICY_TERMS, "Terms of Service"),
        (POLICY_COOKIE, "Cookie Policy"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="policy_acceptances",
    )
    policy_type = models.CharField(max_length=30, choices=POLICY_TYPE_CHOICES)
    version = models.CharField(max_length=40)
    accepted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # "-id" as a tiebreaker: two acceptances created in the same clock
        # tick (accepted_at has second/microsecond resolution depending on
        # the database) must still sort newest-first deterministically.
        ordering = ["-accepted_at", "-id"]
        indexes = [models.Index(fields=["user", "policy_type"])]

    def __str__(self):
        return f"{self.user.email} accepted {self.policy_type} v{self.version}"


class CookieConsent(models.Model):
    """The authenticated user's current cookie preference. "Necessary" is
    never actually optional (the app can't function without an auth
    cookie/token), so it isn't a real field to toggle — only tracked here
    for a complete audit record. `analytics`/`marketing` default to False:
    non-essential tracking must not activate before explicit consent.

    This is the authenticated-user record only. A signed-out visitor's
    preference lives in the browser (localStorage) and is what actually
    gates ad/analytics script loading before any account exists — this
    table exists for cross-device consistency and an audit trail once a
    user has an account, not as the sole enforcement point.
    """

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="cookie_consent",
    )
    analytics = models.BooleanField(default=False)
    marketing = models.BooleanField(default=False)
    consent_version = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.email} cookie consent (v{self.consent_version})"


class LoginOTP(models.Model):
    """
    A pending second-factor challenge. It exists only once the first factor
    (password or Google) has passed, which is what lets the verify step
    trust an email address on its own.

    METHOD_EMAIL carries the emailed code in ``otp``; METHOD_TOTP leaves it
    blank and the code is checked against the user's authenticator app.
    """

    METHOD_EMAIL = "email"
    METHOD_TOTP = "totp"
    METHOD_CHOICES = [
        (METHOD_EMAIL, "Emailed code"),
        (METHOD_TOTP, "Authenticator app"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="login_otps",
    )

    otp = models.CharField(
        max_length=6,
        blank=True,
    )

    method = models.CharField(
        max_length=10,
        choices=METHOD_CHOICES,
        default=METHOD_EMAIL,
    )

    # Wrong guesses against this challenge; see PasswordResetOTP.attempts.
    attempts = models.PositiveSmallIntegerField(
        default=0,
    )

    is_verified = models.BooleanField(
        default=False,
    )

    expires_at = models.DateTimeField()

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-created_at"]

    def is_expired(self):
        return timezone.now() > self.expires_at

    def __str__(self):
        return f"{self.user.email} - Login OTP"


class TwoFactorBackupCode(models.Model):
    """Single-use recovery codes, shown once when the authenticator app is
    set up, for signing in without the phone. Only a keyed hash is stored:
    the codes carry ~50 bits of randomness, so a fast HMAC is enough and a
    slow password hash would only make each sign-in check slower."""

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="backup_codes",
    )

    code_hash = models.CharField(
        max_length=64,
        db_index=True,
    )

    used_at = models.DateTimeField(
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    def __str__(self):
        return f"{self.user.email} backup code ({'used' if self.used_at else 'unused'})"
