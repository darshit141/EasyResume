from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken

from authentication.models import User
from authentication.views.login_view import LoginAPIView


def make_user(email="jane@example.com", phone="1234567890", password="Safe-Passw0rd!"):
    return User.objects.create_user(
        name="Jane Doe",
        email=email,
        phone=phone,
        password=password,
    )


class TokenRefreshAPITests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client = APIClient()

    def test_refresh_returns_new_access_token(self):
        refresh = RefreshToken.for_user(self.user)

        response = self.client.post(
            "/api/auth/refresh/", {"refresh": str(refresh)}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)

    def test_refresh_with_invalid_token_is_rejected(self):
        response = self.client.post(
            "/api/auth/refresh/", {"refresh": "not-a-real-token"}, format="json"
        )

        self.assertEqual(response.status_code, 401)

    def test_refresh_rotates_and_returns_a_new_refresh_token(self):
        # ROTATE_REFRESH_TOKENS: every refresh call must issue a fresh
        # refresh token alongside the new access token, not just an access
        # token — the frontend depends on this to keep refreshing at all
        # (see intelliresume-react's api.js refreshAccessToken()).
        refresh = RefreshToken.for_user(self.user)

        response = self.client.post(
            "/api/auth/refresh/", {"refresh": str(refresh)}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("refresh", response.data)
        self.assertNotEqual(response.data["refresh"], str(refresh))

    def test_a_rotated_out_refresh_token_cannot_be_reused(self):
        # The actual security property rotation buys: a stolen refresh
        # token is single-use, not valid for its full remaining lifetime.
        refresh = RefreshToken.for_user(self.user)

        first_use = self.client.post(
            "/api/auth/refresh/", {"refresh": str(refresh)}, format="json"
        )
        self.assertEqual(first_use.status_code, 200)

        second_use = self.client.post(
            "/api/auth/refresh/", {"refresh": str(refresh)}, format="json"
        )
        self.assertEqual(second_use.status_code, 401)

    def test_refresh_via_httponly_cookie(self):
        refresh = RefreshToken.for_user(self.user)
        self.client.cookies["refresh_token"] = str(refresh)

        # POST without refresh token in body — must be read from HttpOnly cookie
        response = self.client.post("/api/auth/refresh/", {}, format="json")

        self.assertEqual(response.status_code, 200)
        self.assertIn("access", response.data)
        self.assertIn("refresh_token", response.cookies)
        cookie = response.cookies["refresh_token"]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Lax")
        # Ensure CSP header is present
        self.assertIn("Content-Security-Policy", response.headers)


class LogoutAPITests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.client = APIClient()

    def authenticate(self):
        self.client.force_authenticate(user=self.user)

    def test_logout_blacklists_refresh_token(self):
        refresh = RefreshToken.for_user(self.user)
        self.authenticate()

        response = self.client.post(
            "/api/auth/logout/", {"refresh": str(refresh)}, format="json"
        )
        self.assertEqual(response.status_code, 200)

        self.client.force_authenticate(user=None)
        refresh_response = self.client.post(
            "/api/auth/refresh/", {"refresh": str(refresh)}, format="json"
        )
        self.assertEqual(refresh_response.status_code, 401)

    def test_logout_requires_authentication(self):
        refresh = RefreshToken.for_user(self.user)

        response = self.client.post(
            "/api/auth/logout/", {"refresh": str(refresh)}, format="json"
        )

        self.assertEqual(response.status_code, 401)

    def test_logout_with_invalid_token_is_rejected(self):
        self.authenticate()

        response = self.client.post(
            "/api/auth/logout/", {"refresh": "garbage"}, format="json"
        )

        self.assertEqual(response.status_code, 400)

    def test_logout_with_httponly_cookie(self):
        refresh = RefreshToken.for_user(self.user)
        self.authenticate()
        self.client.cookies["refresh_token"] = str(refresh)

        # Empty body — refresh token taken from cookie and deleted
        response = self.client.post("/api/auth/logout/", {}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertIn("refresh_token", response.cookies)
        self.assertEqual(response.cookies["refresh_token"].value, "")

        # Verify token was blacklisted
        self.client.force_authenticate(user=None)
        refresh_response = self.client.post(
            "/api/auth/refresh/", {"refresh": str(refresh)}, format="json"
        )
        self.assertEqual(refresh_response.status_code, 401)


class ProfileAPITests(TestCase):
    def setUp(self):
        self.user = make_user()
        self.other_user = make_user(email="john@example.com", phone="0987654321")
        self.client = APIClient()

    def authenticate(self, user=None):
        self.client.force_authenticate(user=user or self.user)

    def test_get_profile_returns_current_user_data(self):
        self.authenticate()

        response = self.client.get("/api/auth/me/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["email"], "jane@example.com")
        self.assertEqual(response.data["name"], "Jane Doe")
        self.assertEqual(response.data["phone"], "1234567890")
        # is_staff must be present — is_staff-gated UI (e.g. the frontend's
        # Admin Panel nav link) depends on it actually reaching the client.
        self.assertIn("is_staff", response.data)
        self.assertFalse(response.data["is_staff"])

    def test_get_profile_requires_authentication(self):
        response = self.client.get("/api/auth/me/")

        self.assertEqual(response.status_code, 401)

    def test_patch_profile_updates_name_and_phone(self):
        self.authenticate()

        response = self.client.patch(
            "/api/auth/me/",
            {"name": "Jane A. Doe", "phone": "1112223330"},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.name, "Jane A. Doe")
        self.assertEqual(self.user.phone, "1112223330")

    def test_patch_profile_ignores_email_field(self):
        self.authenticate()

        response = self.client.patch(
            "/api/auth/me/", {"email": "changed@example.com"}, format="json"
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "jane@example.com")

    def test_patch_profile_rejects_duplicate_phone(self):
        self.authenticate()

        response = self.client.patch(
            "/api/auth/me/", {"phone": self.other_user.phone}, format="json"
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("phone", response.data)

    def test_patch_profile_allows_keeping_own_phone_unchanged(self):
        self.authenticate()

        response = self.client.patch(
            "/api/auth/me/", {"phone": self.user.phone}, format="json"
        )

        self.assertEqual(response.status_code, 200)


class ChangePasswordAPITests(TestCase):
    def setUp(self):
        self.user = make_user(password="Old-Passw0rd!")
        self.client = APIClient()

    def authenticate(self):
        self.client.force_authenticate(user=self.user)

    def url(self):
        return "/api/auth/change-password/"

    def test_change_password_success(self):
        self.authenticate()

        response = self.client.post(
            self.url(),
            {
                "current_password": "Old-Passw0rd!",
                "new_password": "New-Passw0rd!",
                "confirm_new_password": "New-Passw0rd!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("New-Passw0rd!"))
        self.assertFalse(self.user.check_password("Old-Passw0rd!"))

    def test_change_password_wrong_current_password(self):
        self.authenticate()

        response = self.client.post(
            self.url(),
            {
                "current_password": "wrong-password",
                "new_password": "New-Passw0rd!",
                "confirm_new_password": "New-Passw0rd!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Old-Passw0rd!"))

    def test_change_password_mismatched_confirmation(self):
        self.authenticate()

        response = self.client.post(
            self.url(),
            {
                "current_password": "Old-Passw0rd!",
                "new_password": "New-Passw0rd!",
                "confirm_new_password": "Different-Passw0rd!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_change_password_weak_new_password_is_rejected(self):
        self.authenticate()

        response = self.client.post(
            self.url(),
            {
                "current_password": "Old-Passw0rd!",
                "new_password": "weak",
                "confirm_new_password": "weak",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)

    def test_change_password_requires_authentication(self):
        response = self.client.post(
            self.url(),
            {
                "current_password": "Old-Passw0rd!",
                "new_password": "New-Passw0rd!",
                "confirm_new_password": "New-Passw0rd!",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 401)


class ThrottlingAPITests(TestCase):
    def setUp(self):
        cache.clear()
        self.client = APIClient()

    def tearDown(self):
        cache.clear()

    def test_auth_sensitive_endpoint_is_throttled_after_rate_exceeded(self):
        # Both DEFAULT_THROTTLE_CLASSES (-> APIView.throttle_classes) and
        # DEFAULT_THROTTLE_RATES (-> ScopedRateThrottle.THROTTLE_RATES) are
        # bound once at import time from api_settings, so
        # override_settings(REST_FRAMEWORK=...) does not affect either —
        # patch the actual class attributes read at request time. Throttling
        # is otherwise disabled during `manage.py test` (see
        # config/settings.py RUNNING_TESTS) so the rest of the suite's rapid
        # automated calls never trip it; this test re-enables it locally.
        with patch.object(LoginAPIView, "throttle_classes", [ScopedRateThrottle]), \
                patch.dict(ScopedRateThrottle.THROTTLE_RATES, {"auth_sensitive": "2/min"}):
            payload = {"email": "nobody@example.com", "password": "wrong-password"}

            first = self.client.post("/api/auth/login/", payload, format="json")
            second = self.client.post("/api/auth/login/", payload, format="json")
            third = self.client.post("/api/auth/login/", payload, format="json")

        self.assertNotEqual(first.status_code, 429)
        self.assertNotEqual(second.status_code, 429)
        self.assertEqual(third.status_code, 429)
