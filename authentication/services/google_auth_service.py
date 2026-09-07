"""Sign in with Google.

The entire security of this feature is token verification, so it is worth
being explicit about what is checked and why:

* **Signature**, against Google's published keys. Establishes that Google
  issued the token at all.
* **Audience** (`aud`) must equal one of our own client ids. This is the
  check people skip and it is the one that matters most: an ID token is a
  signed assertion Google hands to *every* application, so a token minted
  for some unrelated app is perfectly valid and, without this check, could
  be replayed here to sign in as its owner.
* **Issuer** (`iss`) must be Google. `verify_oauth2_token` enforces this.
* **Expiry**, likewise enforced by the library.
* **`email_verified`** must be true. Google Workspace administrators can
  create accounts with arbitrary, unproven email addresses; accepting one
  would let someone claim an address they do not own and, through the
  linking rule below, take over an existing IntelliResume account.

Account resolution, in order:

1. Match on `google_sub`. It is immutable, so this keeps working after a
   user changes the email on their Google account.
2. Otherwise match on email and *link* the Google identity to that account.
   Safe only because step 4 above proved Google verified the address.
3. Otherwise create an account.

No OTP is sent. Google has already authenticated the user — including
whatever second factor they have on their Google account, which is
typically stronger than a six-digit code we email to the very address an
attacker would need to have compromised anyway. Requiring our own OTP on
top would be friction without a corresponding gain.
"""

import logging

from django.conf import settings
from django.db import IntegrityError, transaction
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token
from rest_framework import status
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.models import LoginOTP, User

logger = logging.getLogger(__name__)


class GoogleAuthError(Exception):
    def __init__(self, message, code, http_status=status.HTTP_401_UNAUTHORIZED):
        self.message = message
        self.code = code
        self.status = http_status
        super().__init__(message)


class GoogleAuthService:
    @staticmethod
    def allowed_audiences():
        ids = []
        if settings.GOOGLE_OAUTH_CLIENT_ID:
            ids.append(settings.GOOGLE_OAUTH_CLIENT_ID)
        ids.extend(getattr(settings, "GOOGLE_OAUTH_EXTRA_CLIENT_IDS", []) or [])
        return ids

    @staticmethod
    def verify(credential):
        """Return Google's verified claims, or raise GoogleAuthError."""
        audiences = GoogleAuthService.allowed_audiences()
        if not audiences:
            raise GoogleAuthError(
                "Signing in with Google isn't configured on this server.",
                code="GOOGLE_SIGN_IN_UNAVAILABLE",
                http_status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        # `verify_oauth2_token` takes a single audience, so each configured
        # client id is tried in turn. Every attempt still performs the full
        # signature/issuer/expiry check — this widens *which* of our own apps
        # a token may come from, never what counts as a valid token.
        last_error = None
        for audience in audiences:
            try:
                claims = google_id_token.verify_oauth2_token(
                    credential, google_requests.Request(), audience
                )
                break
            except ValueError as exc:
                last_error = exc
                claims = None
        if claims is None:
            logger.warning("Google ID token rejected: %s", last_error)
            raise GoogleAuthError(
                "That Google sign-in couldn't be verified. Please try again.",
                code="GOOGLE_TOKEN_INVALID",
            )

        if not claims.get("email"):
            raise GoogleAuthError(
                "That Google account didn't share an email address.",
                code="GOOGLE_EMAIL_MISSING",
            )

        # A string "true" would sail past a truthiness check, so compare
        # explicitly rather than trusting the shape of the claim.
        if claims.get("email_verified") not in (True, "true", "True"):
            raise GoogleAuthError(
                "That Google account's email address isn't verified.",
                code="GOOGLE_EMAIL_UNVERIFIED",
            )

        return claims

    @staticmethod
    def sign_in(credential):
        claims = GoogleAuthService.verify(credential)
        sub = claims["sub"]
        email = claims["email"].strip().lower()
        name = (claims.get("name") or "").strip() or email.split("@")[0]

        user, created = GoogleAuthService._resolve_user(sub, email, name)

        if not user.is_active:
            raise GoogleAuthError(
                "This account has been deactivated.",
                code="ACCOUNT_INACTIVE",
                http_status=status.HTTP_403_FORBIDDEN,
            )

        # Any OTP left over from an abandoned password login must not stay
        # replayable against the verify-otp endpoint once a session exists.
        LoginOTP.objects.filter(user=user).delete()

        refresh = RefreshToken.for_user(user)
        logger.info(
            "Google sign-in succeeded user_id=%s created=%s", user.id, created
        )
        return {
            "user": user,
            "created": created,
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }

    @staticmethod
    def _resolve_user(sub, email, name):
        existing = User.objects.filter(google_sub=sub).first()
        if existing:
            return existing, False

        by_email = User.objects.filter(email__iexact=email).first()
        if by_email:
            # Linking, not creating: this is the returning user who signed up
            # with a password and is now using the Google button. Their
            # password keeps working.
            by_email.google_sub = sub
            by_email.save(update_fields=["google_sub"])
            return by_email, False

        try:
            with transaction.atomic():
                user = User.objects.create(
                    email=email,
                    name=name,
                    google_sub=sub,
                )
                # There is no password to check against, so make sure the
                # password path can never authenticate this account.
                user.set_unusable_password()
                user.save(update_fields=["password"])
                return user, True
        except IntegrityError:
            # Two simultaneous first sign-ins for the same account: whoever
            # lost the race just reads the row the winner created.
            user = User.objects.filter(google_sub=sub).first() or User.objects.filter(
                email__iexact=email
            ).first()
            if user is None:
                raise
            return user, False
