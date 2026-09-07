"""Tests for Sign in with Google.

Most of these exist to pin down the two checks that make this safe rather
than merely working: the audience check, and the email-verified check.
Both are easy to drop while the happy path keeps passing, and either one
missing turns this endpoint into an account-takeover primitive.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from authentication.models import LoginOTP, User
from authentication.services.google_auth_service import (
    GoogleAuthError,
    GoogleAuthService,
)

CLIENT_ID = "test-client-id.apps.googleusercontent.com"
URL = "/api/auth/google/"


def claims(**overrides):
    payload = {
        "sub": "google-sub-1",
        "email": "person@example.com",
        "email_verified": True,
        "name": "Person Example",
        "aud": CLIENT_ID,
        "iss": "https://accounts.google.com",
    }
    payload.update(overrides)
    return payload


@override_settings(GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID, GOOGLE_OAUTH_EXTRA_CLIENT_IDS=[])
class GoogleSignInAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def post(self, verified_claims=None, credential="token", side_effect=None):
        target = "authentication.services.google_auth_service.google_id_token.verify_oauth2_token"
        with patch(target) as verify:
            if side_effect:
                verify.side_effect = side_effect
            else:
                verify.return_value = verified_claims or claims()
            response = self.client.post(URL, {"credential": credential}, format="json")
        return response

    # -- happy paths ------------------------------------------------------

    def test_first_sign_in_creates_an_account_and_returns_tokens(self):
        response = self.post()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["created"])
        self.assertFalse(response.data["otp_required"])
        self.assertTrue(response.data["access"])
        self.assertTrue(response.data["refresh"])

        user = User.objects.get(email="person@example.com")
        self.assertEqual(user.google_sub, "google-sub-1")
        self.assertEqual(user.name, "Person Example")

    def test_second_sign_in_reuses_the_same_account(self):
        self.post()
        response = self.post()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["created"])
        self.assertEqual(User.objects.count(), 1)

    def test_account_is_matched_by_sub_even_when_the_google_email_changed(self):
        """The whole reason `sub` is the key: an email change must not orphan
        the account or create a second one."""
        self.post()

        response = self.post(claims(email="renamed@example.com"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 1)
        self.assertFalse(response.data["created"])

    def test_existing_password_account_is_linked_not_duplicated(self):
        existing = User.objects.create_user(
            name="Person", email="person@example.com", phone="123", password="pw"
        )

        response = self.post()

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data["created"])
        self.assertEqual(User.objects.count(), 1)
        existing.refresh_from_db()
        self.assertEqual(existing.google_sub, "google-sub-1")
        # Linking must not cost them their password.
        self.assertTrue(existing.check_password("pw"))

    def test_google_created_account_cannot_be_logged_into_with_a_password(self):
        self.post()
        user = User.objects.get(email="person@example.com")

        self.assertFalse(user.has_usable_password())

    def test_pending_login_otp_is_cleared_so_it_cannot_be_replayed(self):
        user = User.objects.create_user(
            name="Person", email="person@example.com", phone="123", password="pw"
        )
        from django.utils import timezone
        from datetime import timedelta

        LoginOTP.objects.create(
            user=user, otp="123456", expires_at=timezone.now() + timedelta(minutes=5)
        )

        self.post()

        self.assertEqual(LoginOTP.objects.filter(user=user).count(), 0)

    # -- the checks that make it safe -------------------------------------

    def test_token_for_another_application_is_rejected(self):
        """The audience check. Google signs an ID token for every app; one
        minted for someone else's app must not be a valid login here."""
        def wrong_audience(credential, request, audience):
            raise ValueError(f"Token has wrong audience, expected {audience}")

        response = self.post(side_effect=wrong_audience)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], "GOOGLE_TOKEN_INVALID")
        self.assertEqual(User.objects.count(), 0)

    def test_unverified_google_email_is_rejected(self):
        """Without this, a Workspace admin could mint an account claiming an
        address they don't own and be linked straight into it."""
        response = self.post(claims(email_verified=False))

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], "GOOGLE_EMAIL_UNVERIFIED")
        self.assertEqual(User.objects.count(), 0)

    def test_unverified_email_cannot_hijack_an_existing_account(self):
        User.objects.create_user(
            name="Victim", email="person@example.com", phone="123", password="pw"
        )

        response = self.post(claims(email_verified=False))

        self.assertEqual(response.status_code, 401)
        self.assertIsNone(User.objects.get(email="person@example.com").google_sub)

    def test_string_false_email_verified_is_not_treated_as_truthy(self):
        response = self.post(claims(email_verified="false"))

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], "GOOGLE_EMAIL_UNVERIFIED")

    def test_invalid_signature_is_rejected(self):
        response = self.post(side_effect=ValueError("Invalid token signature"))

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], "GOOGLE_TOKEN_INVALID")

    def test_missing_email_claim_is_rejected(self):
        response = self.post(claims(email=None))

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.data["code"], "GOOGLE_EMAIL_MISSING")

    def test_deactivated_account_cannot_sign_in(self):
        user = User.objects.create_user(
            name="Person", email="person@example.com", phone="123", password="pw"
        )
        user.is_active = False
        user.save(update_fields=["is_active"])

        response = self.post()

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "ACCOUNT_INACTIVE")

    def test_missing_credential_is_a_validation_error(self):
        response = self.client.post(URL, {}, format="json")

        self.assertEqual(response.status_code, 400)

    # -- configuration ----------------------------------------------------

    @override_settings(GOOGLE_OAUTH_CLIENT_ID="", GOOGLE_OAUTH_EXTRA_CLIENT_IDS=[])
    def test_unconfigured_server_says_so_instead_of_failing_like_a_bad_login(self):
        response = self.client.post(URL, {"credential": "token"}, format="json")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data["code"], "GOOGLE_SIGN_IN_UNAVAILABLE")

    @override_settings(
        GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID,
        GOOGLE_OAUTH_EXTRA_CLIENT_IDS=["extension-client.apps.googleusercontent.com"],
    )
    def test_extra_client_id_is_accepted_for_the_extension(self):
        """The extension registers as its own OAuth client, so its tokens
        carry a different audience — but each one is still checked exactly."""
        seen = []

        def only_extension(credential, request, audience):
            seen.append(audience)
            if audience != "extension-client.apps.googleusercontent.com":
                raise ValueError("wrong audience")
            return claims(aud=audience)

        with patch(
            "authentication.services.google_auth_service.google_id_token.verify_oauth2_token",
            side_effect=only_extension,
        ):
            response = self.client.post(URL, {"credential": "t"}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(seen, [CLIENT_ID, "extension-client.apps.googleusercontent.com"])


@override_settings(GOOGLE_OAUTH_CLIENT_ID=CLIENT_ID, GOOGLE_OAUTH_EXTRA_CLIENT_IDS=[])
class GoogleAuthServiceTests(TestCase):
    def test_verify_raises_when_unconfigured(self):
        with override_settings(GOOGLE_OAUTH_CLIENT_ID="", GOOGLE_OAUTH_EXTRA_CLIENT_IDS=[]):
            with self.assertRaises(GoogleAuthError) as ctx:
                GoogleAuthService.verify("token")

        self.assertEqual(ctx.exception.code, "GOOGLE_SIGN_IN_UNAVAILABLE")

    def test_name_falls_back_to_the_email_local_part(self):
        with patch(
            "authentication.services.google_auth_service.google_id_token.verify_oauth2_token",
            return_value=claims(name=""),
        ):
            GoogleAuthService.sign_in("token")

        self.assertEqual(User.objects.get(email="person@example.com").name, "person")

    def test_email_is_normalised_to_lowercase(self):
        with patch(
            "authentication.services.google_auth_service.google_id_token.verify_oauth2_token",
            return_value=claims(email="Person@Example.COM"),
        ):
            GoogleAuthService.sign_in("token")

        self.assertTrue(User.objects.filter(email="person@example.com").exists())
