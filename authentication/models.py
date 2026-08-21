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

    is_active = models.BooleanField(
        default=True,
    )

    is_staff = models.BooleanField(
        default=False,
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
    Model to store login OTPs.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="login_otps",
    )

    otp = models.CharField(
        max_length=6,
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