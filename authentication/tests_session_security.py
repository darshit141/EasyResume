"""Signed-in sensitive actions must not be a way round the sign-in limits:
change password, delete account and the two-factor changes all count wrong
passwords/codes against the account lockout, and changing the password ends
every other session.
"""

from django.test import RequestFactory, SimpleTestCase, TestCase
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken
from rest_framework_simplejwt.tokens import RefreshToken

from api.models import SystemConfiguration
from api.throttles import ClientIPScopedRateThrottle, client_ip
from authentication.models import User
from authentication.services import auth_security
from authentication.utils import totp

PASSWORD = "safe-password-123"
NEW_PASSWORD = "Brand-new-pass-456!"


class _Signed(TestCase):
    def setUp(self):
        SystemConfiguration.objects.all().delete()
        self.user = User.objects.create_user(
            name="Sess User", email="sess@example.com", phone="5550002222", password=PASSWORD,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def change(self, current, new=NEW_PASSWORD):
        return self.client.post(
            "/api/auth/change-password/",
            {"current_password": current, "new_password": new, "confirm_new_password": new},
            format="json",
        )

    def delete(self, password):
        return self.client.post("/api/auth/account/delete/", {"password": password}, format="json")


class ChangePasswordLockoutTests(_Signed):
    def test_wrong_current_password_locks_the_account_after_the_limit(self):
        limit = auth_security.get_policy().max_failed_logins
        for _ in range(limit - 1):
            self.assertEqual(self.change("wrong-password").status_code, 400)
        locked = self.change("wrong-password")
        self.assertEqual(locked.status_code, 429)
        self.assertEqual(locked.json()["code"], "ACCOUNT_LOCKED")
        self.assertGreater(locked.json()["retry_after_seconds"], 0)

    def test_locked_account_refuses_even_the_right_password(self):
        for _ in range(auth_security.get_policy().max_failed_logins):
            self.change("wrong-password")
        self.assertEqual(self.change(PASSWORD).status_code, 429)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(PASSWORD))

    def test_lockout_also_blocks_signing_in(self):
        for _ in range(auth_security.get_policy().max_failed_logins):
            self.change("wrong-password")
        response = APIClient().post(
            "/api/auth/login/", {"email": self.user.email, "password": PASSWORD}, format="json"
        )
        self.assertEqual(response.status_code, 429)


class ChangePasswordSessionTests(_Signed):
    def test_success_revokes_other_sessions_and_hands_back_fresh_tokens(self):
        other_device = RefreshToken.for_user(self.user)

        response = self.change(PASSWORD)

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["access"] and body["refresh"])
        self.assertTrue(BlacklistedToken.objects.filter(token__jti=other_device["jti"]).exists())
        refreshed = APIClient().post("/api/auth/refresh/", {"refresh": str(other_device)}, format="json")
        self.assertEqual(refreshed.status_code, 401)
        # The tokens handed back are for this device and still work.
        fresh = APIClient().post("/api/auth/refresh/", {"refresh": body["refresh"]}, format="json")
        self.assertEqual(fresh.status_code, 200)

    def test_new_password_works_and_old_one_does_not(self):
        self.change(PASSWORD)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PASSWORD))
        self.assertFalse(self.user.check_password(PASSWORD))


class AccountDeletionLockoutTests(_Signed):
    def test_wrong_password_counts_towards_lockout_and_nothing_is_deleted(self):
        limit = auth_security.get_policy().max_failed_logins
        for _ in range(limit - 1):
            self.assertEqual(self.delete("nope").status_code, 400)
        self.assertEqual(self.delete("nope").status_code, 429)
        self.assertEqual(self.delete(PASSWORD).status_code, 429)
        self.assertTrue(User.objects.filter(pk=self.user.pk).exists())

    def test_right_password_still_deletes(self):
        self.assertEqual(self.delete(PASSWORD).status_code, 200)
        self.assertFalse(User.objects.filter(pk=self.user.pk).exists())


class TwoFactorChangeLockoutTests(_Signed):
    def _enable(self):
        setup = self.client.post("/api/auth/2fa/totp/setup/", format="json").json()
        step_code = totp.code_for(setup["secret"])
        confirmed = self.client.post("/api/auth/2fa/totp/confirm/", {"code": step_code}, format="json")
        self.assertEqual(confirmed.status_code, 200, confirmed.content)

    def test_wrong_codes_when_turning_off_count_towards_lockout(self):
        self._enable()
        limit = auth_security.get_policy().max_failed_logins
        last = None
        for _ in range(limit):
            last = self.client.post(
                "/api/auth/2fa/totp/disable/", {"password": PASSWORD, "code": "000000"}, format="json"
            )
        self.assertEqual(last.status_code, 429)
        self.assertEqual(last.json()["code"], "ACCOUNT_LOCKED")
        self.user.refresh_from_db()
        self.assertTrue(self.user.totp_enabled)

    def test_wrong_password_when_turning_off_counts_too(self):
        self._enable()
        limit = auth_security.get_policy().max_failed_logins
        last = None
        for _ in range(limit):
            last = self.client.post(
                "/api/auth/2fa/totp/disable/", {"password": "nope", "code": "000000"}, format="json"
            )
        self.assertEqual(last.status_code, 429)

    def test_wrong_codes_against_backup_code_regeneration_lock_the_account(self):
        self._enable()
        limit = auth_security.get_policy().max_failed_logins
        last = None
        for _ in range(limit):
            last = self.client.post("/api/auth/2fa/backup-codes/", {"code": "000000"}, format="json")
        self.assertEqual(last.status_code, 429)


class ClientIPTests(SimpleTestCase):
    def ip(self, remote, forwarded=None):
        extra = {"REMOTE_ADDR": remote}
        if forwarded is not None:
            extra["HTTP_X_FORWARDED_FOR"] = forwarded
        return client_ip(RequestFactory().get("/", **extra))

    def test_plain_connection_uses_the_socket_address(self):
        self.assertEqual(self.ip("93.184.216.34"), "93.184.216.34")

    def test_a_forged_leading_entry_is_ignored(self):
        # nginx appends the real peer on the right; the visitor wrote the left.
        self.assertEqual(self.ip("172.18.0.1", "6.6.6.6, 93.184.216.34"), "93.184.216.34")

    def test_rotating_the_forged_entry_does_not_change_the_address(self):
        a = self.ip("172.18.0.1", "1.1.1.1, 93.184.216.34")
        b = self.ip("172.18.0.1", "2.2.2.2, 93.184.216.34")
        self.assertEqual(a, b)

    def test_edge_network_hops_are_skipped(self):
        # visitor -> edge (172.68.1.1) -> nginx, with a forged lead entry.
        self.assertEqual(
            self.ip("172.18.0.1", "6.6.6.6, 93.184.216.34, 172.68.1.1"), "93.184.216.34"
        )

    def test_garbage_in_the_header_falls_back_to_the_socket(self):
        self.assertEqual(self.ip("93.184.216.34", "not-an-ip, also-bad"), "93.184.216.34")

    def test_only_infrastructure_addresses_falls_back_to_the_socket(self):
        self.assertEqual(self.ip("172.18.0.1", "172.68.1.1"), "172.18.0.1")


class ThrottleBucketTests(SimpleTestCase):
    def test_throttle_keys_on_the_real_address_not_the_header(self):
        throttle = ClientIPScopedRateThrottle()
        one = RequestFactory().get("/", REMOTE_ADDR="172.18.0.1", HTTP_X_FORWARDED_FOR="1.1.1.1, 93.184.216.34")
        two = RequestFactory().get("/", REMOTE_ADDR="172.18.0.1", HTTP_X_FORWARDED_FOR="2.2.2.2, 93.184.216.34")
        self.assertEqual(throttle.get_ident(one), throttle.get_ident(two))
        other = RequestFactory().get("/", REMOTE_ADDR="172.18.0.1", HTTP_X_FORWARDED_FOR="93.184.216.77")
        self.assertNotEqual(throttle.get_ident(one), throttle.get_ident(other))
