"""Gemini: surviving overloaded, rate-limited and discontinued models.

Written after an outage where every Flash model returned 503 at once while
Flash-Lite answered, and the old client's hardcoded fallback list - five
Flash models - never reached it. Nothing here relies on a model id written in
the client: every model comes from configuration or from the model list the
test hands back, which is the point.

Each HTTP response is scripted one urlopen call at a time, so the order of the
calls is itself under test.
"""

import io
import json
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from django.core.cache import cache
from django.test import SimpleTestCase, TestCase, override_settings

from api.models import SystemConfiguration
from api.utils.secrets import encrypt_value
from resumes.services.llm_service import (
    GeminiLLMService,
    LLMAuthError,
    LLMModelUnavailableError,
    LLMQuotaExceededError,
    LLMServiceError,
    _auto_gemini_fallbacks,
    _model_rejects_thinking_budget,
    get_llm_service,
)

BASE = "https://generativelanguage.googleapis.com/v1beta"
FAMILIES = ["flash"]
EXCLUDE = [
    "image", "tts", "audio", "transcribe", "live", "embedding", "computer-use",
    "robotics", "customtools", "research", "antigravity", "omni", "preview", "exp",
]

# A slice of what Google listed for a real key on 2026-09-15, including the
# kinds of model that must never be picked automatically.
LIVE_MODELS = [
    "gemini-2.5-flash", "gemini-2.5-flash-image", "gemini-2.5-flash-lite",
    "gemini-2.5-flash-preview-tts", "gemini-2.5-pro", "gemini-3-flash-preview",
    "gemini-3.1-flash-lite", "gemini-3.1-flash-lite-preview", "gemini-3.1-pro-preview",
    "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.5-transcribe",
    "gemini-3.6-flash", "gemini-3.7-flash", "gemini-3.8-flash",
    "gemini-flash-latest", "gemini-flash-lite-latest", "gemini-omni-1.1-flash",
    "gemini-pro-latest", "gemma-4-31b-it", "deep-research-preview-04-2026",
]


class _Response:
    def __init__(self, raw):
        self.raw = raw.encode("utf-8") if isinstance(raw, str) else raw

    def read(self):
        return self.raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def ok(payload=None):
    text = json.dumps(payload or {"ok": True})
    return _Response(json.dumps({"candidates": [{"content": {"parts": [{"text": text}]}}]}))


def google_error(code, status, message, reason=None):
    error = {"code": code, "status": status, "message": message}
    if reason:
        error["details"] = [{"reason": reason}]
    return HTTPError(f"{BASE}/x", code, status, {}, io.BytesIO(json.dumps({"error": error}).encode("utf-8")))


def models_list(names):
    return _Response(json.dumps({
        "models": [{"name": f"models/{name}", "supportedGenerationMethods": ["generateContent"]} for name in names]
    }))


OVERLOADED = lambda: google_error(503, "UNAVAILABLE", "This model is currently experiencing high demand.")  # noqa: E731


class Script:
    """urlopen stand-in that replays scripted responses in order."""

    def __init__(self, *steps):
        self.steps = list(steps)
        self.requests = []

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if not self.steps:
            raise AssertionError(f"Unexpected extra request: {request.full_url}")
        step = self.steps.pop(0)
        if isinstance(step, BaseException):
            raise step
        return step

    @property
    def generated_with(self):
        return [
            request.full_url.split("/models/")[1].split(":")[0]
            for request in self.requests
            if ":generateContent" in request.full_url
        ]

    @property
    def listings(self):
        return [request for request in self.requests if request.full_url.startswith(f"{BASE}/models?")]

    def payload(self, index):
        generation = [request for request in self.requests if ":generateContent" in request.full_url]
        return json.loads(generation[index].data.decode("utf-8"))


def make(model="gemini-3.8-flash", fallback_models=(), discover=True, max_retries=0, key="test-key",
         families=FAMILIES, exclude=EXCLUDE, max_attempts=4):
    return GeminiLLMService(
        api_key=key,
        model=model,
        timeout=5,
        max_retries=max_retries,
        temperature=0,
        base_url=BASE,
        fallback_models=fallback_models,
        discover_models=discover,
        auto_families=families,
        auto_exclude=exclude,
        max_model_attempts=max_attempts,
        model_list_ttl=3600,
    )


class GeminiTestBase(SimpleTestCase):
    def setUp(self):
        cache.clear()
        sleep = patch("resumes.services.llm_service.time.sleep")
        sleep.start()
        self.addCleanup(sleep.stop)

    def script(self, *steps):
        script = Script(*steps)
        patcher = patch("resumes.services.llm_service.urlopen", side_effect=script)
        patcher.start()
        self.addCleanup(patcher.stop)
        return script


class ModelChainTests(GeminiTestBase):
    def test_reaches_flash_lite_when_every_flash_model_is_overloaded(self):
        """The outage itself: all Flash models 503 together, Flash-Lite is up."""
        script = self.script(models_list(LIVE_MODELS), OVERLOADED(), OVERLOADED(), ok({"skills": ["Go"]}))
        service = make()

        self.assertEqual(service.generate_structured("prompt", {"type": "object"}), {"skills": ["Go"]})
        self.assertEqual(
            script.generated_with,
            ["gemini-3.8-flash", "gemini-flash-latest", "gemini-flash-lite-latest"],
        )
        self.assertEqual(service.last_model_used, "gemini-flash-lite-latest")

    def test_a_discontinued_configured_model_is_skipped_before_any_request(self):
        available = [name for name in LIVE_MODELS if name != "gemini-3.5-flash"]
        script = self.script(models_list(available), ok())
        service = make(model="gemini-3.5-flash")

        with self.assertLogs("resumes.services.llm_service", "WARNING") as logs:
            service.generate_structured("prompt", {})

        self.assertNotIn("gemini-3.5-flash", script.generated_with)
        self.assertEqual(script.generated_with, ["gemini-flash-latest"])
        self.assertIn("not offered", "\n".join(logs.output))

    def test_with_no_model_configured_the_live_list_still_serves_requests(self):
        """No model id anywhere - not in code, not in configuration - and it
        still works. This is what makes a discontinued model a non-event."""
        script = self.script(models_list(LIVE_MODELS), ok())
        service = make(model="")

        service.generate_structured("prompt", {})

        self.assertEqual(script.generated_with, ["gemini-flash-latest"])

    def test_without_discovery_only_configured_models_are_tried(self):
        script = self.script(OVERLOADED(), ok())
        service = make(discover=False, fallback_models=["gemini-3.5-flash-lite"])

        service.generate_structured("prompt", {})

        self.assertEqual(script.generated_with, ["gemini-3.8-flash", "gemini-3.5-flash-lite"])
        self.assertEqual(script.listings, [])

    def test_nothing_configured_and_nothing_discoverable_fails_clearly(self):
        script = self.script()
        service = make(model="", discover=False)

        with self.assertLogs("resumes.services.llm_service", "ERROR"):
            with self.assertRaises(LLMModelUnavailableError):
                service.generate_structured("prompt", {})
        self.assertEqual(script.requests, [])

    def test_quota_exhaustion_on_every_model_is_reported_as_quota(self):
        quota = lambda: google_error(429, "RESOURCE_EXHAUSTED", "You exceeded your current quota.")  # noqa: E731
        script = self.script(quota(), quota(), quota())
        service = make(discover=False, fallback_models=["gemini-3.7-flash", "gemini-3.5-flash-lite"])

        with self.assertRaises(LLMQuotaExceededError):
            service.generate_structured("prompt", {})
        self.assertEqual(len(script.generated_with), 3)

    def test_an_overloaded_model_fails_over_at_once_when_another_is_available(self):
        """Retrying an overloaded model before failing over only adds seconds
        to every request during an outage - measured at ~7s."""
        script = self.script(OVERLOADED(), ok())
        service = make(discover=False, max_retries=2, fallback_models=["gemini-3.5-flash-lite"])

        service.generate_structured("prompt", {})

        self.assertEqual(script.generated_with, ["gemini-3.8-flash", "gemini-3.5-flash-lite"])

    def test_the_last_model_left_is_retried(self):
        script = self.script(OVERLOADED(), ok())
        service = make(discover=False, max_retries=1)

        service.generate_structured("prompt", {})

        self.assertEqual(script.generated_with, ["gemini-3.8-flash", "gemini-3.8-flash"])

    def test_the_number_of_models_tried_is_capped(self):
        script = self.script(models_list(LIVE_MODELS), OVERLOADED(), OVERLOADED())
        service = make(max_attempts=2)

        with self.assertRaises(LLMServiceError):
            service.generate_structured("prompt", {})
        self.assertEqual(len(script.generated_with), 2)


class AutoFallbackSelectionTests(SimpleTestCase):
    def test_interleaves_full_and_lite_models_and_skips_unsuitable_ones(self):
        picked = _auto_gemini_fallbacks(LIVE_MODELS, FAMILIES, EXCLUDE)

        self.assertEqual(
            picked[:4],
            ["gemini-flash-latest", "gemini-flash-lite-latest", "gemini-3.8-flash", "gemini-3.5-flash-lite"],
        )
        for name in picked:
            for unsuitable in ("image", "tts", "preview", "pro", "omni", "transcribe", "gemma"):
                self.assertNotIn(unsuitable, name)

    def test_what_counts_as_a_fallback_is_configuration(self):
        without_lite = _auto_gemini_fallbacks(LIVE_MODELS, FAMILIES, EXCLUDE + ["lite"])
        self.assertTrue(without_lite)
        self.assertFalse(any("lite" in name for name in without_lite))

    def test_no_families_turns_automatic_fallback_off(self):
        self.assertEqual(_auto_gemini_fallbacks(LIVE_MODELS, [], EXCLUDE), [])


class KeyHandlingTests(GeminiTestBase):
    def test_a_rejected_key_fails_fast_without_trying_other_models(self):
        script = self.script(google_error(400, "INVALID_ARGUMENT", "API key not valid.", "API_KEY_INVALID"))
        service = make(discover=False, fallback_models=["gemini-3.5-flash-lite"])

        with self.assertRaises(LLMAuthError) as raised:
            service.generate_structured("prompt", {})

        self.assertEqual(len(script.requests), 1)
        self.assertEqual(raised.exception.code, "LLM_AUTH_FAILED")
        # Existing handlers that catch "model unavailable" keep working.
        self.assertIsInstance(raised.exception, LLMModelUnavailableError)

    def test_a_key_rejected_while_listing_models_is_an_auth_error(self):
        script = self.script(google_error(400, "INVALID_ARGUMENT", "API key not valid.", "API_KEY_INVALID"))

        with self.assertRaises(LLMAuthError):
            make().generate_structured("prompt", {})
        self.assertEqual(len(script.requests), 1)

    def test_a_missing_key_makes_no_request(self):
        script = self.script()

        with self.assertLogs("resumes.services.llm_service", "ERROR"):
            with self.assertRaises(LLMAuthError):
                make(key="").generate_structured("prompt", {})
        self.assertEqual(script.requests, [])

    def test_readiness_reports_a_rejected_key(self):
        self.script(google_error(403, "PERMISSION_DENIED", "Permission denied."))

        with self.assertRaises(LLMAuthError):
            make().check_model_available()

    def test_readiness_spends_no_generation_call(self):
        script = self.script(models_list(LIVE_MODELS))

        self.assertTrue(make().check_model_available())
        self.assertEqual(script.generated_with, [])


class DiscoveryTests(GeminiTestBase):
    def test_the_model_list_is_cached_per_key(self):
        script = self.script(models_list(LIVE_MODELS), ok(), ok(), models_list(LIVE_MODELS), ok())

        make(key="key-one").generate_structured("prompt", {})
        make(key="key-one").generate_structured("prompt", {})
        self.assertEqual(len(script.listings), 1)

        # A replaced key must see its own list straight away, not the old one's.
        make(key="key-two").generate_structured("prompt", {})
        self.assertEqual(len(script.listings), 2)

    def test_an_unreachable_model_list_falls_back_to_the_configured_models(self):
        script = self.script(URLError("temporary failure"), ok())

        make(fallback_models=["gemini-3.5-flash-lite"]).generate_structured("prompt", {})

        self.assertEqual(script.generated_with, ["gemini-3.8-flash"])


class ThinkingBudgetTests(GeminiTestBase):
    def test_a_model_that_rejects_a_zero_budget_is_retried_without_it_and_remembered(self):
        script = self.script(google_error(400, "INVALID_ARGUMENT", "Request contains an invalid argument."), ok())
        service = make(model="gemini-3.5-flash-lite", discover=False)

        service.generate_structured("prompt", {})

        self.assertEqual(script.generated_with, ["gemini-3.5-flash-lite", "gemini-3.5-flash-lite"])
        self.assertIn("thinkingConfig", script.payload(0)["generationConfig"])
        self.assertNotIn("thinkingConfig", script.payload(1)["generationConfig"])
        self.assertTrue(_model_rejects_thinking_budget("gemini-3.5-flash-lite"))

        # The next request goes straight to the version that works.
        second = self.script(ok())
        make(model="gemini-3.5-flash-lite", discover=False).generate_structured("prompt", {})
        self.assertEqual(len(second.requests), 1)
        self.assertNotIn("thinkingConfig", second.payload(0)["generationConfig"])

    def test_an_unrelated_400_teaches_nothing(self):
        """Only remembered when dropping the budget actually fixed it."""
        bad = lambda: google_error(400, "INVALID_ARGUMENT", "Request contains an invalid argument.")  # noqa: E731
        script = self.script(bad(), bad())

        with self.assertRaises(LLMServiceError):
            make(discover=False).generate_structured("prompt", {})

        self.assertEqual(len(script.generated_with), 2)
        self.assertFalse(_model_rejects_thinking_budget("gemini-3.8-flash"))


class ProbeTests(GeminiTestBase):
    def test_a_probe_reports_the_failure_instead_of_raising(self):
        self.script(OVERLOADED())

        ok_flag, detail = make(discover=False).probe_model("gemini-3.8-flash")

        self.assertFalse(ok_flag)
        self.assertIn("503 UNAVAILABLE", detail)


@override_settings(LLM_FALLBACK_PROVIDER="")
class AdminPanelConfigurationTests(TestCase):
    """Everything the client runs with comes from the admin panel first."""

    def setUp(self):
        SystemConfiguration.objects.all().delete()
        cache.clear()

    def _row(self, key, value, value_type="string"):
        SystemConfiguration.objects.create(key=key, value=value, value_type=value_type, category="llm")

    @override_settings(GEMINI_API_KEY="env-key")
    def test_a_key_saved_in_the_panel_wins_over_the_environment(self):
        self._row("GEMINI_API_KEY", encrypt_value("panel-key"))
        self.assertEqual(get_llm_service(provider="gemini").api_key, "panel-key")

    @override_settings(GEMINI_API_KEY="env-key")
    def test_the_environment_key_is_used_until_one_is_saved(self):
        self.assertEqual(get_llm_service(provider="gemini").api_key, "env-key")

    @override_settings(GEMINI_API_KEY="")
    def test_whitespace_around_a_pasted_key_is_removed(self):
        self._row("GEMINI_API_KEY", encrypt_value("  panel-key \n"))
        self.assertEqual(get_llm_service(provider="gemini").api_key, "panel-key")

    @override_settings(GEMINI_API_KEY="env-key")
    def test_an_undecryptable_key_falls_back_to_the_environment_and_is_logged(self):
        self._row("GEMINI_API_KEY", "enc:v1:not-a-real-token")

        with self.assertLogs("api.utils.config", "ERROR") as logs:
            service = get_llm_service(provider="gemini")

        self.assertEqual(service.api_key, "env-key")
        self.assertIn("could not be decrypted", "\n".join(logs.output))

    def test_models_fallbacks_and_discovery_rules_are_read_from_the_panel(self):
        self._row("GEMINI_MODEL", "gemini-from-panel")
        self._row("GEMINI_FALLBACK_MODELS", "gemini-a, gemini-b", "list")
        self._row("GEMINI_MODEL_DISCOVERY", "false", "boolean")
        self._row("GEMINI_AUTO_FALLBACK_FAMILIES", "flash, pro", "list")
        self._row("GEMINI_AUTO_EXCLUDE", "image", "list")
        self._row("GEMINI_MAX_MODEL_ATTEMPTS", "6", "integer")

        service = get_llm_service(provider="gemini")

        self.assertEqual(service.model, "gemini-from-panel")
        self.assertEqual(service.fallback_models, ["gemini-a", "gemini-b"])
        self.assertFalse(service.discover_models)
        self.assertEqual(service.auto_families, ["flash", "pro"])
        self.assertEqual(service.auto_exclude, ["image"])
        self.assertEqual(service.max_model_attempts, 6)
