"""Bring-your-own-key: storage, isolation, and what it does and does not buy.

The security assertions here are the point of the file. A feature that asks
users to hand over a credential has to prove, in tests, that the credential
is encrypted, is never echoed back, is never used for anybody else, and can
always be withdrawn.
"""

from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from api.models import SystemConfiguration
from api.utils.secrets import (
    DOMAIN_SYSTEM_CONFIGURATION,
    DOMAIN_USER_PROVIDER_KEY,
    SecretDecryptionError,
    decrypt_value,
    encrypt_value,
    is_encrypted,
)
from authentication.models import User
from billing.entitlements import FREE_ENTITLEMENTS
from billing.models import UserAIKey
from billing.services.entitlements_service import (
    EntitlementsService,
    PlanLimitReachedError,
)
from billing.services.user_ai_key_service import (
    BYOKDisabledError,
    UserAIKeyError,
    UserAIKeyService,
)

VALID_KEY = "AIzaSyTESTTESTTESTTESTTESTTEST1234"
OTHER_KEY = "AIzaSyOTHEROTHEROTHEROTHEROTH9999"


def _make_user(email):
    return User.objects.create_user(
        name="Key Owner",
        email=email,
        phone=f"8{abs(hash(email)) % 1000000000:09d}",
        password="safe-password",
    )


def _enable_byok(enabled=True):
    SystemConfiguration.objects.update_or_create(
        key="BYOK_ENABLED",
        defaults={
            "value": "true" if enabled else "false",
            "value_type": "boolean",
            "category": "ai",
            "description": "Allow users to supply their own provider API key.",
        },
    )


class _AcceptingClient:
    """Stands in for a provider that says the key is fine."""

    def __init__(self, api_key=None):
        self.api_key = api_key

    def check_model_available(self):
        return True


def _accept_validation():
    return patch(
        "resumes.services.llm_service.build_client_with_key",
        side_effect=lambda provider, api_key: _AcceptingClient(api_key),
    )


class BYOKSwitchTests(TestCase):
    def setUp(self):
        self.user = _make_user("switch@example.com")

    def test_byok_is_off_until_an_operator_turns_it_on(self):
        """Off by default is the right default for a feature that accepts
        other people's credentials — it should be a deliberate decision."""
        self.assertFalse(UserAIKeyService.enabled())

    def test_saving_a_key_is_refused_while_the_feature_is_off(self):
        with _accept_validation():
            with self.assertRaises(BYOKDisabledError):
                UserAIKeyService.set_key(self.user, "gemini", VALID_KEY)

        self.assertFalse(UserAIKey.objects.exists())

    def test_an_already_saved_key_is_ignored_once_the_feature_is_switched_off(self):
        """Turning the feature off has to stop keys being *used*, not merely
        stop new ones being accepted — otherwise switching it off in an
        incident leaves every existing key still in play."""
        _enable_byok(True)
        with _accept_validation():
            UserAIKeyService.set_key(self.user, "gemini", VALID_KEY)

        _enable_byok(False)
        self.assertIsNone(UserAIKeyService.active_key_for(self.user, "gemini"))
        self.assertFalse(UserAIKeyService.has_active_key(self.user))


class BYOKStorageTests(TestCase):
    def setUp(self):
        _enable_byok(True)
        self.user = _make_user("storage@example.com")

    def test_key_is_validated_against_the_provider_before_being_stored(self):
        with _accept_validation() as build:
            UserAIKeyService.set_key(self.user, "gemini", VALID_KEY)

        build.assert_called_once_with("gemini", VALID_KEY)

    def test_key_is_encrypted_at_rest(self):
        with _accept_validation():
            UserAIKeyService.set_key(self.user, "gemini", VALID_KEY)

        record = UserAIKey.objects.get(user=self.user, provider="gemini")
        self.assertTrue(is_encrypted(record.encrypted_key))
        self.assertNotIn(VALID_KEY, record.encrypted_key)

    def test_only_the_last_four_characters_are_kept_in_the_clear(self):
        with _accept_validation():
            UserAIKeyService.set_key(self.user, "gemini", VALID_KEY)

        record = UserAIKey.objects.get(user=self.user, provider="gemini")
        self.assertEqual(record.last_four, VALID_KEY[-4:])
        self.assertNotIn(VALID_KEY[:-4], str(record))

    def test_a_key_pasted_with_surrounding_whitespace_still_works(self):
        with _accept_validation():
            UserAIKeyService.set_key(self.user, "gemini", f"  {VALID_KEY}\n")

        self.assertEqual(UserAIKeyService.active_key_for(self.user, "gemini"), VALID_KEY)

    def test_a_rejected_key_is_not_stored(self):
        from resumes.services.llm_service import LLMAuthError

        class _Rejecting:
            def check_model_available(self):
                raise LLMAuthError()

        with patch("resumes.services.llm_service.build_client_with_key", return_value=_Rejecting()):
            with self.assertRaises(UserAIKeyError) as ctx:
                UserAIKeyService.set_key(self.user, "gemini", VALID_KEY)

        self.assertEqual(ctx.exception.code, "INVALID_API_KEY")
        self.assertFalse(UserAIKey.objects.exists())

    def test_an_unreachable_provider_is_reported_as_transient_not_as_a_bad_key(self):
        """Telling someone their key is invalid when the provider was merely
        busy sends them off to regenerate a perfectly good credential."""
        from resumes.services.llm_service import LLMServiceError

        class _Unreachable:
            def check_model_available(self):
                raise LLMServiceError()

        with patch("resumes.services.llm_service.build_client_with_key", return_value=_Unreachable()):
            with self.assertRaises(UserAIKeyError) as ctx:
                UserAIKeyService.set_key(self.user, "gemini", VALID_KEY)

        self.assertEqual(ctx.exception.code, "PROVIDER_UNREACHABLE")
        self.assertFalse(UserAIKey.objects.exists())

    def test_an_obviously_incomplete_key_is_rejected_without_calling_the_provider(self):
        with patch("resumes.services.llm_service.build_client_with_key") as build:
            with self.assertRaises(UserAIKeyError):
                UserAIKeyService.set_key(self.user, "gemini", "too-short")

        build.assert_not_called()

    def test_a_provider_without_byok_support_is_refused(self):
        with _accept_validation():
            with self.assertRaises(UserAIKeyError) as ctx:
                UserAIKeyService.set_key(self.user, "ollama", VALID_KEY)

        self.assertEqual(ctx.exception.code, "UNSUPPORTED_PROVIDER")

    def test_saving_again_replaces_rather_than_duplicates(self):
        with _accept_validation():
            UserAIKeyService.set_key(self.user, "gemini", VALID_KEY)
            UserAIKeyService.set_key(self.user, "gemini", OTHER_KEY)

        self.assertEqual(UserAIKey.objects.filter(user=self.user).count(), 1)
        self.assertEqual(UserAIKeyService.active_key_for(self.user, "gemini"), OTHER_KEY)


class BYOKEncryptionDomainTests(TestCase):
    """The two secret stores must not share a key.

    Without domain separation, any path that lets a value be written into
    SystemConfiguration would also let it be read back as somebody's personal
    provider key, and a stolen user-key ciphertext could be replanted as a
    server credential.
    """

    def test_a_user_key_ciphertext_does_not_decrypt_as_system_configuration(self):
        ciphertext = encrypt_value(VALID_KEY, domain=DOMAIN_USER_PROVIDER_KEY)

        with self.assertRaises(SecretDecryptionError):
            decrypt_value(ciphertext, domain=DOMAIN_SYSTEM_CONFIGURATION)

    def test_a_system_configuration_ciphertext_does_not_decrypt_as_a_user_key(self):
        ciphertext = encrypt_value(VALID_KEY, domain=DOMAIN_SYSTEM_CONFIGURATION)

        with self.assertRaises(SecretDecryptionError):
            decrypt_value(ciphertext, domain=DOMAIN_USER_PROVIDER_KEY)

    def test_the_default_domain_is_still_system_configuration(self):
        """Existing encrypted rows were written before the domain argument
        existed; changing the default would make every one of them
        unreadable."""
        self.assertEqual(
            decrypt_value(encrypt_value(VALID_KEY), domain=DOMAIN_SYSTEM_CONFIGURATION),
            VALID_KEY,
        )

    def test_an_undecryptable_key_falls_back_instead_of_failing(self):
        _enable_byok(True)
        user = _make_user("rotated@example.com")
        with _accept_validation():
            UserAIKeyService.set_key(user, "gemini", VALID_KEY)

        # What a rotated CONFIG_ENCRYPTION_KEY looks like from here.
        UserAIKey.objects.filter(user=user).update(encrypted_key="enc:v1:not-a-real-token")

        self.assertIsNone(UserAIKeyService.active_key_for(user, "gemini"))


class BYOKIsolationTests(TestCase):
    """One user's credential must never serve another user's request."""

    def setUp(self):
        _enable_byok(True)
        self.owner = _make_user("owner@example.com")
        self.stranger = _make_user("stranger@example.com")
        with _accept_validation():
            UserAIKeyService.set_key(self.owner, "gemini", VALID_KEY)

    def test_the_owner_gets_their_own_key(self):
        self.assertEqual(UserAIKeyService.active_key_for(self.owner, "gemini"), VALID_KEY)

    def test_another_user_never_sees_it(self):
        self.assertIsNone(UserAIKeyService.active_key_for(self.stranger, "gemini"))
        self.assertFalse(UserAIKeyService.has_active_key(self.stranger))

    def test_an_anonymous_caller_never_sees_it(self):
        self.assertIsNone(UserAIKeyService.active_key_for(None, "gemini"))

    def test_the_llm_client_is_built_with_the_owners_key(self):
        from resumes.services.llm_service import get_llm_service

        service = get_llm_service("gemini", user=self.owner)
        self.assertEqual(service.api_key, VALID_KEY)

    def test_the_llm_client_for_a_stranger_uses_the_server_key(self):
        from resumes.services.llm_service import get_llm_service

        service = get_llm_service("gemini", user=self.stranger)
        self.assertNotEqual(service.api_key, VALID_KEY)

    def test_no_user_means_the_server_key(self):
        from resumes.services.llm_service import get_llm_service

        service = get_llm_service("gemini")
        self.assertNotEqual(service.api_key, VALID_KEY)

    def test_a_paused_key_is_not_used(self):
        UserAIKeyService.set_active(self.owner, "gemini", False)

        self.assertIsNone(UserAIKeyService.active_key_for(self.owner, "gemini"))

    def test_a_key_for_one_provider_is_not_used_for_another(self):
        from resumes.services.llm_service import get_llm_service

        service = get_llm_service("groq", user=self.owner)
        self.assertNotEqual(service.api_key, VALID_KEY)


@override_settings(LLM_PROVIDER="gemini")
class BYOKQuotaTests(TestCase):
    """What a personal key buys — and, just as importantly, what it doesn't.

    Pinned to LLM_PROVIDER="gemini" throughout: these tests are about
    whether a *gemini* key lifts the ceiling for a *gemini* call, and must
    not depend on whatever LLM_PROVIDER happens to be set to in whichever
    environment the suite runs in.
    """

    def setUp(self):
        _enable_byok(True)
        self.user = _make_user("quota@example.com")

    def test_the_ai_ceiling_still_applies_without_a_key(self):
        with patch.dict(FREE_ENTITLEMENTS, {"ai_calls_per_month": 0}):
            with self.assertRaises(PlanLimitReachedError):
                EntitlementsService.check_can_use_ai(self.user)

    def test_a_personal_key_lifts_the_ai_ceiling_for_its_own_provider(self):
        """That ceiling exists only to cap IntelliResume's provider bill. A
        user paying their own provider is not on that bill — for calls
        actually served by that provider."""
        with _accept_validation():
            UserAIKeyService.set_key(self.user, "gemini", VALID_KEY)

        with patch.dict(FREE_ENTITLEMENTS, {"ai_calls_per_month": 0}):
            EntitlementsService.check_can_use_ai(self.user)  # must not raise
            # Explicit and implicit resolution to the key's own provider
            # both count — the server default here *is* gemini.
            EntitlementsService.check_can_use_ai(self.user, provider="gemini")

    def test_a_gemini_key_does_not_lift_the_ceiling_for_a_different_provider(self):
        """The exact bug this guards against: a user's key is only for
        Gemini, so a call that actually resolves to Groq is not covered by
        it and must still be billed to the free allowance — a security
        review flagged the provider-agnostic version of this check as a
        way to permanently bypass the cost-control quota for free."""
        with _accept_validation():
            UserAIKeyService.set_key(self.user, "gemini", VALID_KEY)

        with patch.dict(FREE_ENTITLEMENTS, {"ai_calls_per_month": 0}):
            with self.assertRaises(PlanLimitReachedError):
                EntitlementsService.check_can_use_ai(self.user, provider="groq")

    @override_settings(LLM_PROVIDER="groq")
    def test_a_gemini_key_does_not_lift_the_ceiling_when_the_server_default_is_different(self):
        """The same bug, via the common path: no ai_provider was requested
        at all, so it resolves to whatever the server's own default is —
        here, deliberately not gemini."""
        with _accept_validation():
            UserAIKeyService.set_key(self.user, "gemini", VALID_KEY)

        with patch.dict(FREE_ENTITLEMENTS, {"ai_calls_per_month": 0}):
            with self.assertRaises(PlanLimitReachedError):
                EntitlementsService.check_can_use_ai(self.user)

    def test_a_personal_key_does_not_lift_the_tailored_resume_limit(self):
        """The marketed feature limit is product value, not compute cost."""
        with _accept_validation():
            UserAIKeyService.set_key(self.user, "gemini", VALID_KEY)

        with patch.dict(FREE_ENTITLEMENTS, {"tailored_resumes_per_month": 0}):
            with self.assertRaises(PlanLimitReachedError):
                EntitlementsService.check_can_generate_tailored_resume(self.user)

    def test_a_personal_key_does_not_unlock_paid_features(self):
        from billing.services.entitlements_service import FeatureRequiresProError

        with _accept_validation():
            UserAIKeyService.set_key(self.user, "gemini", VALID_KEY)

        with self.assertRaises(FeatureRequiresProError):
            EntitlementsService.check_feature(self.user, "docx_export")

    def test_usage_summary_reports_the_lifted_ceiling_honestly(self):
        """The summary has to describe the gate that actually runs, or the UI
        counts down toward a limit nobody is enforcing."""
        with _accept_validation():
            UserAIKeyService.set_key(self.user, "gemini", VALID_KEY)

        summary = EntitlementsService.usage_summary(self.user)
        self.assertTrue(summary["uses_own_ai_key"])
        self.assertIsNone(summary["ai_calls_limit"])
        self.assertIsNotNone(summary["tailored_resumes_limit"])

    @override_settings(LLM_PROVIDER="groq")
    def test_usage_summary_does_not_claim_unlimited_for_an_unrelated_key(self):
        """A gemini key must not make the summary tell a user their AI
        allowance is unlimited when an ordinary request — no provider
        specified — actually resolves to Groq and is still metered."""
        with _accept_validation():
            UserAIKeyService.set_key(self.user, "gemini", VALID_KEY)

        summary = EntitlementsService.usage_summary(self.user)
        self.assertFalse(summary["uses_own_ai_key"])
        self.assertIsNotNone(summary["ai_calls_limit"])


class BYOKEndpointTests(TestCase):
    def setUp(self):
        _enable_byok(True)
        self.user = _make_user("endpoint@example.com")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_requires_authentication(self):
        anonymous = APIClient()
        self.assertEqual(anonymous.get("/api/billing/ai-keys/").status_code, 401)

    def test_summary_lists_supported_providers_with_nothing_configured(self):
        response = self.client.get("/api/billing/ai-keys/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data["enabled"])
        self.assertEqual(
            [entry["provider"] for entry in response.data["keys"]], ["gemini", "dahl"]
        )
        self.assertFalse(response.data["keys"][0]["configured"])

    def test_saving_a_key_never_returns_it(self):
        with _accept_validation():
            response = self.client.put(
                "/api/billing/ai-keys/",
                {"provider": "gemini", "api_key": VALID_KEY},
                format="json",
            )

        self.assertEqual(response.status_code, 200)
        body = str(response.data)
        self.assertNotIn(VALID_KEY, body)
        self.assertNotIn(VALID_KEY[:-4], body)
        self.assertEqual(response.data["keys"][0]["masked_key"], f"••••••••{VALID_KEY[-4:]}")

    def test_a_rejected_key_returns_a_clear_error_without_echoing_it(self):
        from resumes.services.llm_service import LLMAuthError

        class _Rejecting:
            def check_model_available(self):
                raise LLMAuthError()

        with patch("resumes.services.llm_service.build_client_with_key", return_value=_Rejecting()):
            response = self.client.put(
                "/api/billing/ai-keys/",
                {"provider": "gemini", "api_key": VALID_KEY},
                format="json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["code"], "INVALID_API_KEY")
        self.assertNotIn(VALID_KEY, str(response.data))

    def test_pausing_and_resuming_a_key(self):
        with _accept_validation():
            self.client.put(
                "/api/billing/ai-keys/",
                {"provider": "gemini", "api_key": VALID_KEY},
                format="json",
            )

        paused = self.client.patch(
            "/api/billing/ai-keys/",
            {"provider": "gemini", "is_active": False},
            format="json",
        )
        self.assertFalse(paused.data["keys"][0]["is_active"])

        resumed = self.client.patch(
            "/api/billing/ai-keys/",
            {"provider": "gemini", "is_active": True},
            format="json",
        )
        self.assertTrue(resumed.data["keys"][0]["is_active"])

    def test_deleting_a_key_removes_the_row_entirely(self):
        with _accept_validation():
            self.client.put(
                "/api/billing/ai-keys/",
                {"provider": "gemini", "api_key": VALID_KEY},
                format="json",
            )

        response = self.client.delete("/api/billing/ai-keys/?provider=gemini")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserAIKey.objects.filter(user=self.user).exists())

    def test_a_key_can_be_withdrawn_even_after_the_feature_is_switched_off(self):
        """Withdrawing a credential must never depend on a server-side
        feature flag — that would leave a user unable to take back something
        they gave us."""
        with _accept_validation():
            self.client.put(
                "/api/billing/ai-keys/",
                {"provider": "gemini", "api_key": VALID_KEY},
                format="json",
            )
        _enable_byok(False)

        response = self.client.delete("/api/billing/ai-keys/?provider=gemini")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserAIKey.objects.filter(user=self.user).exists())

    def test_saving_is_refused_with_403_while_the_feature_is_off(self):
        _enable_byok(False)

        with _accept_validation():
            response = self.client.put(
                "/api/billing/ai-keys/",
                {"provider": "gemini", "api_key": VALID_KEY},
                format="json",
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.data["code"], "BYOK_DISABLED")

    def test_one_user_cannot_see_another_users_key_state(self):
        with _accept_validation():
            self.client.put(
                "/api/billing/ai-keys/",
                {"provider": "gemini", "api_key": VALID_KEY},
                format="json",
            )

        stranger = APIClient()
        stranger.force_authenticate(_make_user("nosy@example.com"))
        response = stranger.get("/api/billing/ai-keys/")

        self.assertFalse(response.data["keys"][0]["configured"])
        self.assertEqual(response.data["keys"][0]["masked_key"], "")
