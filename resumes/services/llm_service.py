"""Provider-neutral structured-generation interface and local Ollama adapter."""

from abc import ABC, abstractmethod
import hashlib
import itertools
import json
import logging
import re
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.cache import cache
from api.utils.config import get_config_value

logger = logging.getLogger(__name__)


class LLMServiceError(Exception):
    """Base class for safe, application-level LLM failures."""

    code = "LLM_UNAVAILABLE"
    message = "AI service is unavailable."

    def __init__(self, message=None):
        super().__init__(message or self.message)


class LLMEmptyResponseError(LLMServiceError):
    code = "LLM_EMPTY_RESPONSE"
    message = "AI service returned an empty response."


class LLMInvalidResponseError(LLMServiceError):
    code = "LLM_INVALID_RESPONSE"
    message = "AI service returned an invalid response."


class LLMSchemaValidationError(LLMServiceError):
    code = "LLM_SCHEMA_VALIDATION_FAILED"
    message = "AI service returned data in an unexpected format."


class LLMModelUnavailableError(LLMServiceError):
    code = "LLM_MODEL_UNAVAILABLE"
    message = "The configured AI model is unavailable."


class LLMTimeoutError(LLMServiceError):
    code = "LLM_TIMEOUT"
    message = "AI service request timed out."


class LLMAuthError(LLMModelUnavailableError):
    """The provider rejected the API key: invalid, revoked, or not allowed
    to use the API.

    A subclass of LLMModelUnavailableError so every existing handler keeps
    treating it as a 503, as before this class existed. What changes is the
    code: a bad key and a missing model need different fixes, and they used
    to be indistinguishable in logs and in stored error codes.
    """

    code = "LLM_AUTH_FAILED"
    message = "The AI provider rejected the configured API key."


class LLMQuotaExceededError(LLMServiceError):
    code = "LLM_QUOTA_EXCEEDED"
    message = "The AI provider's quota for the configured key is exhausted."


SUPPORTED_PROVIDERS = ("ollama", "groq", "gemini", "kira")

# Resume upload/parsing has one extra choice unavailable elsewhere: a
# deterministic, non-AI parser (see StandardResumeParserService). The other
# AI-backed endpoints (job analysis, gap analysis, tailored resume
# generation) require real semantic reasoning a regex engine can't provide,
# so they only ever offer SUPPORTED_PROVIDERS.
RESUME_PARSE_PROVIDERS = SUPPORTED_PROVIDERS + ("standard",)


def _parse_json_content(content):
    if not isinstance(content, str) or not content.strip():
        raise LLMEmptyResponseError()
    cleaned = content.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise LLMInvalidResponseError() from exc


class LLMService(ABC):
    """The only LLM dependency used by application services."""

    @abstractmethod
    def generate_structured(self, prompt, schema):
        """Return parsed JSON produced for the supplied JSON schema."""

    def check_model_available(self):
        """Verify provider reachability and configured-model availability."""
        raise NotImplementedError


class OllamaLLMService(LLMService):
    """Small standard-library client for Ollama's local /api/generate API."""

    def __init__(self, base_url, model, timeout, max_retries, temperature, max_output_tokens=2048, keep_alive="30m"):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens
        self.keep_alive = keep_alive

    def generate_structured(self, prompt, schema):
        if not self.model:
            raise LLMModelUnavailableError()

        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "format": schema,
            "stream": False,
            # Ollama evicts an idle model after 5 minutes by default, so a
            # normally-paced user pays a full cold model load (measured at
            # ~60s on a CPU-only machine) on almost every request — enough
            # on its own to blow the request timeout and surface as a
            # spurious LLM_TIMEOUT. Holding the model resident makes the
            # second and later parses cost only actual inference time.
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": self.temperature,
                # Hard backstop: without this, a schema-constrained small
                # model can fall into a degenerate repetition loop on a
                # field the source text doesn't clearly answer (observed:
                # an empty "languages" list turned into an endless
                # enumeration of world languages) and run for the full
                # context window instead of ever returning.
                "num_predict": self.max_output_tokens,
            },
        }).encode("utf-8")
        request = Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                content = body.get("response")
                if not isinstance(content, str) or not content.strip():
                    raise LLMEmptyResponseError()
                try:
                    return json.loads(content)
                except json.JSONDecodeError as exc:
                    raise LLMInvalidResponseError() from exc
            except HTTPError as exc:
                if exc.code == 404:
                    raise LLMModelUnavailableError() from exc
                raise LLMServiceError() from exc
            except (socket.timeout, TimeoutError) as exc:
                if attempt == self.max_retries:
                    raise LLMTimeoutError() from exc
            except (URLError, ConnectionError) as exc:
                if attempt == self.max_retries:
                    raise LLMServiceError() from exc

        raise LLMServiceError()

    def check_model_available(self):
        if not self.model:
            raise LLMModelUnavailableError()
        request = Request(f"{self.base_url}/api/tags", method="GET")
        try:
            with urlopen(request, timeout=min(self.timeout, 10)) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise LLMServiceError() from exc
        except (socket.timeout, TimeoutError) as exc:
            raise LLMTimeoutError() from exc
        except (URLError, ConnectionError) as exc:
            raise LLMServiceError() from exc

        models = body.get("models")
        if not isinstance(models, list):
            raise LLMInvalidResponseError()
        model_names = {
            item.get("name") or item.get("model")
            for item in models if isinstance(item, dict)
        }
        if self.model not in model_names:
            raise LLMModelUnavailableError()
        return True


class GroqLLMService(LLMService):
    """Client for Groq's free, OpenAI-compatible chat completions API.

    Groq's JSON mode guarantees syntactically valid JSON but not schema
    conformance, so the schema is embedded in the prompt text as an
    instruction; each caller's own ``_validate``/``validate_*`` step is what
    actually enforces the contract (same as it already does for Ollama).
    """

    def __init__(self, api_key, model, timeout, max_retries, temperature, base_url, max_output_tokens=2048):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.temperature = temperature
        self.base_url = base_url.rstrip("/")
        self.max_output_tokens = max_output_tokens

    def generate_structured(self, prompt, schema):
        if not self.model or not self.api_key:
            raise LLMModelUnavailableError()

        full_prompt = (
            f"{prompt}\n\nRespond with a single JSON object only, with no "
            f"surrounding text, conforming exactly to this JSON schema:\n"
            f"{json.dumps(schema)}"
        )
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": full_prompt}],
            "response_format": {"type": "json_object"},
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
        }).encode("utf-8")
        request = Request(
            f"{self.base_url}/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "IntelliResume/1.0",
            },
            method="POST",
        )

        for attempt in range(self.max_retries + 1):
            try:
                with urlopen(request, timeout=self.timeout) as response:
                    body = json.loads(response.read().decode("utf-8"))
                choices = body.get("choices")
                if not isinstance(choices, list) or not choices:
                    raise LLMEmptyResponseError()
                content = (choices[0].get("message") or {}).get("content")
                if not isinstance(content, str) or not content.strip():
                    raise LLMEmptyResponseError()
                try:
                    return json.loads(content)
                except json.JSONDecodeError as exc:
                    raise LLMInvalidResponseError() from exc
            except HTTPError as exc:
                if exc.code in (401, 403):
                    raise LLMAuthError() from exc
                if exc.code == 404:
                    raise LLMModelUnavailableError() from exc
                raise LLMServiceError() from exc
            except (socket.timeout, TimeoutError) as exc:
                if attempt == self.max_retries:
                    raise LLMTimeoutError() from exc
            except (URLError, ConnectionError) as exc:
                if attempt == self.max_retries:
                    raise LLMServiceError() from exc

        raise LLMServiceError()

    def check_model_available(self):
        if not self.model or not self.api_key:
            raise LLMModelUnavailableError()
        request = Request(
            f"{self.base_url}/models",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "User-Agent": "IntelliResume/1.0",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=min(self.timeout, 10)) as response:
                body = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code in (401, 403):
                raise LLMAuthError() from exc
            if exc.code == 404:
                raise LLMModelUnavailableError() from exc
            raise LLMServiceError() from exc
        except (socket.timeout, TimeoutError) as exc:
            raise LLMTimeoutError() from exc
        except (URLError, ConnectionError) as exc:
            raise LLMServiceError() from exc

        models = body.get("data")
        if not isinstance(models, list):
            raise LLMInvalidResponseError()
        model_ids = {item.get("id") for item in models if isinstance(item, dict)}
        if self.model not in model_ids:
            raise LLMModelUnavailableError()
        return True


class KiraLLMService(GroqLLMService):
    """Client for Kira (kiraai.vn), an OpenAI-compatible aggregator API.

    Kira exposes the exact same ``/chat/completions`` and ``/models``
    contract as Groq — Bearer auth, ``response_format: {"type":
    "json_object"}``, ``choices[0].message.content`` — so it reuses the
    Groq client wholesale rather than duplicating it. It is a separate
    class only so ``get_llm_service`` / ``_build_client`` can point at it
    by provider name and callers can assert which provider they got.

    It is configured with its own base URL, key and model (see
    ``KIRA_*`` in settings). It tends to offer a much larger context
    window and higher rate limits than the free tiers of the other
    providers, which is why a deployment sharing one key across many
    users would pick it as the default (``LLM_PROVIDER=kira``).
    """


def _to_gemini_schema(schema):
    """Convert a draft-07-style JSON schema into Gemini's OpenAPI-subset
    schema: uppercase type names, and ``["string", "null"]`` unions turned
    into ``nullable: true`` since Gemini has no type-array syntax."""
    if not isinstance(schema, dict):
        return schema

    result = {}
    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        non_null = [item for item in schema_type if item != "null"]
        result["type"] = (non_null[0] if non_null else "string").upper()
        if "null" in schema_type:
            result["nullable"] = True
    elif isinstance(schema_type, str):
        result["type"] = schema_type.upper()

    if "properties" in schema:
        result["properties"] = {
            key: _to_gemini_schema(value) for key, value in schema["properties"].items()
        }
    if "items" in schema:
        result["items"] = _to_gemini_schema(schema["items"])
    if "required" in schema:
        result["required"] = schema["required"]
    if "enum" in schema:
        result["enum"] = schema["enum"]
    if "description" in schema:
        result["description"] = schema["description"]
    return result


# --- Gemini --------------------------------------------------------------------
#
# No model id is written anywhere in this client. Which models to use, what to
# fall back to, and which kinds of model never to pick automatically are all
# runtime configuration (the admin Configuration tab, with settings.py only as
# a first-boot fallback), and the models that actually exist come from Google's
# live model list. A discontinued model is therefore skipped on its own, and
# anything else is an edit in the admin panel - never a code change and a
# redeploy, which is what the last outage needed.

_GEMINI_VERSION_RE = re.compile(r"^gemini-(\d+(?:\.\d+)?)-")
# How long a "this model rejects thinkingBudget=0" lesson is kept.
_THINKING_REJECTED_TTL = 7 * 24 * 60 * 60

_PROBE_PROMPT = 'Reply with the JSON object {"ok": true} and nothing else.'
_PROBE_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}


def _dedupe(items):
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result


def _as_list(value):
    """Items from a comma-separated string or a list - SystemConfiguration
    hands back one and settings.py the other."""
    if not value:
        return []
    parts = value.split(",") if isinstance(value, str) else value
    return _dedupe(str(part).strip() for part in parts)


def _as_int(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _google_error_details(exc):
    """``(status, message, reasons)`` from a Google API error response.

    Google explains every failure in the body - API_KEY_INVALID,
    RESOURCE_EXHAUSTED, "model not found" - and the old client threw that
    away, so a rejected key and an overloaded model looked identical in the
    logs. Never raises: a malformed body just yields less detail.
    """
    try:
        raw = exc.read().decode("utf-8", "replace")
    except Exception:
        raw = ""
    try:
        error = json.loads(raw).get("error") or {}
    except (ValueError, AttributeError):
        return "", raw[:300], []
    reasons = [
        detail.get("reason")
        for detail in error.get("details") or []
        if isinstance(detail, dict) and detail.get("reason")
    ]
    return str(error.get("status") or ""), str(error.get("message") or "")[:300], reasons


def _is_key_rejection(code, message, reasons):
    """A key problem is never fixed by trying another model, so it must not be
    handled like an overloaded one. Google reports an invalid key as a 400
    with reason API_KEY_INVALID, not as a 401."""
    return code in (401, 403) or "API_KEY_INVALID" in reasons or "api key not valid" in message.lower()


def _thinking_rejected_key(model):
    return "llm:gemini:no-thinking-budget:" + hashlib.sha256(model.encode("utf-8")).hexdigest()[:24]


def _model_rejects_thinking_budget(model):
    return bool(cache.get(_thinking_rejected_key(model)))


def _remember_thinking_budget_rejected(model):
    # In the shared cache (Redis in production), so the web workers and the
    # Celery worker learn it once between them rather than once apiece.
    cache.set(_thinking_rejected_key(model), True, _THINKING_REJECTED_TTL)


def _auto_gemini_fallbacks(available, families, exclude):
    """Models from the live list worth falling back to, best first.

    ``families`` - a model's id must contain one of these (e.g. "flash").
    ``exclude`` - a model's id must contain none of these (e.g. "image",
    "preview"). Both are configuration, not code: when Google ships a new kind
    of model, an admin can include or exclude it without a deploy. An empty
    ``families`` turns automatic fallback off.

    Lighter variants (ids containing "lite") are interleaved with the full
    models rather than listed after them: they run on separate capacity, and
    the outage this exists for was every Flash model returning 503 at the same
    moment while Flash-Lite answered - a chain of only full models fails on
    every step.
    """
    families = [family.lower() for family in _as_list(families)]
    exclude = [marker.lower() for marker in _as_list(exclude)]
    if not families:
        return []

    def usable(name):
        lowered = name.lower()
        return (
            lowered.startswith("gemini-")
            and any(family in lowered for family in families)
            and not any(marker in lowered for marker in exclude)
        )

    def rank(name):
        # `-latest` aliases first: Google repoints them when it retires a
        # model, so they are the ids least likely to disappear.
        if name.endswith("-latest"):
            return (0, 0.0)
        match = _GEMINI_VERSION_RE.match(name)
        return (1, -float(match.group(1))) if match else (2, 0.0)

    names = sorted({name for name in available if usable(name)}, key=lambda name: (rank(name), name))
    full = [name for name in names if "lite" not in name.lower()]
    lite = [name for name in names if "lite" in name.lower()]
    return [name for pair in itertools.zip_longest(full, lite) for name in pair if name]


class GeminiLLMService(LLMService):
    """Client for Google Gemini's generateContent API, using its native
    ``responseSchema`` support for real schema-constrained JSON output.

    Survives the ways a Gemini model stops answering, handling each
    differently because each means something different:

    * **503 overloaded** - the next model at once; only the last one left is retried.
    * **429 out of quota** - the next model; free-tier quotas are per model.
    * **404 discontinued** - the next model. With discovery on, a discontinued
      model is skipped before any request, because it is missing from the list.
    * **Key rejected** - fails at once with ``LLMAuthError``. No other model can
      fix a bad key, and trying them all only buries the real cause.

    The chain is the configured model, then the configured fallbacks, then
    models picked from the live list by the configured families/exclusions.
    """

    def __init__(
        self,
        api_key,
        model,
        timeout,
        max_retries,
        temperature,
        base_url,
        max_output_tokens=8192,
        fallback_models=(),
        discover_models=True,
        auto_families=(),
        auto_exclude=(),
        max_model_attempts=4,
        model_list_ttl=3600,
    ):
        self.api_key = (api_key or "").strip()
        # May be empty: with discovery on, the live list can still serve
        # requests with nothing configured at all.
        self.model = (model or "").strip()
        self.timeout = timeout
        self.max_retries = max_retries
        self.temperature = temperature
        self.base_url = base_url.rstrip("/")
        self.max_output_tokens = max_output_tokens
        self.fallback_models = _as_list(fallback_models)
        self.discover_models = bool(discover_models)
        self.auto_families = _as_list(auto_families)
        self.auto_exclude = _as_list(auto_exclude)
        self.max_model_attempts = max(_as_int(max_model_attempts, 4), 1)
        self.model_list_ttl = max(_as_int(model_list_ttl, 3600), 60)
        # Which model actually answered the last request. The chain can quietly
        # serve from a fallback, and anyone judging output quality needs that.
        self.last_model_used = None

    # -- model selection ------------------------------------------------------

    def available_models(self):
        """Ids this key can call generateContent on, or ``None`` when the list
        can't be fetched (network trouble, a Google outage, discovery off).

        Cached per key, so a key change picks up a fresh list immediately and
        an unchanged key costs one listing per cache period, not one per
        request. Raises ``LLMAuthError`` when Google rejects the key - the one
        failure here that must not be quietly worked around.
        """
        if not self.discover_models:
            return None

        digest = hashlib.sha256(f"{self.base_url}|{self.api_key}".encode("utf-8")).hexdigest()[:24]
        cache_key = f"llm:gemini:models:{digest}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        names = []
        page_token = None
        for _ in range(5):
            url = f"{self.base_url}/models?pageSize=1000"
            if page_token:
                url += f"&pageToken={quote(page_token)}"
            request = Request(
                url,
                headers={"x-goog-api-key": self.api_key, "User-Agent": "IntelliResume/1.0"},
                method="GET",
            )
            try:
                with urlopen(request, timeout=min(self.timeout, 15)) as response:
                    body = json.loads(response.read().decode("utf-8"))
            except HTTPError as exc:
                status_name, message, reasons = _google_error_details(exc)
                if _is_key_rejection(exc.code, message, reasons):
                    logger.error(
                        "Gemini rejected the API key while listing models (HTTP %s %s): %s",
                        exc.code, status_name, message,
                    )
                    raise LLMAuthError() from exc
                logger.warning(
                    "Could not list Gemini models (HTTP %s %s): %s - trying configured models only.",
                    exc.code, status_name, message,
                )
                return None
            except (socket.timeout, TimeoutError, URLError, ConnectionError, ValueError) as exc:
                logger.warning("Could not list Gemini models (%s) - trying configured models only.", exc)
                return None

            for item in body.get("models") or []:
                if isinstance(item, dict) and "generateContent" in (item.get("supportedGenerationMethods") or []):
                    name = str(item.get("name") or "")
                    names.append(name[len("models/"):] if name.startswith("models/") else name)
            page_token = body.get("nextPageToken")
            if not page_token:
                break

        cache.set(cache_key, names, self.model_list_ttl)
        return names

    def candidate_models(self):
        """The models a request will try, in order."""
        configured = _dedupe([self.model, *self.fallback_models])
        available = self.available_models()

        if not available:
            chain = configured
        else:
            offered = set(available)
            discontinued = [model for model in configured if model not in offered]
            if discontinued:
                logger.warning(
                    "Configured Gemini model(s) %s are not offered to this API key (discontinued "
                    "or not enabled) - skipping them. Update GEMINI_MODEL / GEMINI_FALLBACK_MODELS "
                    "in the admin Configuration tab.",
                    ", ".join(discontinued),
                )
            chain = [model for model in configured if model in offered] + _auto_gemini_fallbacks(
                available, self.auto_families, self.auto_exclude
            )

        return _dedupe(chain)[: max(self.max_model_attempts, len(configured))]

    # -- generation -------------------------------------------------------------

    def generate_structured(self, prompt, schema):
        if not self.api_key:
            logger.error("No Gemini API key is configured - add GEMINI_API_KEY in the admin Configuration tab.")
            raise LLMAuthError()

        candidates = self.candidate_models()
        if not candidates:
            logger.error(
                "No usable Gemini model: GEMINI_MODEL is empty and no model could be discovered. "
                "Set GEMINI_MODEL in the admin Configuration tab."
            )
            raise LLMModelUnavailableError()

        response_schema = _to_gemini_schema(schema)
        last_exc = None

        for index, model in enumerate(candidates):
            is_last_model = index == len(candidates) - 1
            attempts = self.max_retries + 1
            send_thinking = not _model_rejects_thinking_budget(model)
            retried_without_thinking = False
            attempt = 0

            while attempt < attempts:
                try:
                    body = self._post_generate(model, prompt, response_schema, send_thinking)
                except HTTPError as exc:
                    status_name, message, reasons = _google_error_details(exc)
                    if _is_key_rejection(exc.code, message, reasons):
                        logger.error("Gemini rejected the API key (HTTP %s %s): %s", exc.code, status_name, message)
                        raise LLMAuthError() from exc
                    if exc.code == 400 and send_thinking:
                        # Some models refuse a zero thinking budget (verified:
                        # the Flash-Lite models). Google's error doesn't say so,
                        # so try once without it; it is only remembered if that
                        # then works, so an unrelated 400 teaches nothing wrong.
                        send_thinking = False
                        retried_without_thinking = True
                        continue
                    if exc.code == 404:
                        logger.warning("Gemini model %s is not available (HTTP 404): %s", model, message)
                        last_exc = LLMModelUnavailableError()
                        break
                    if exc.code == 429:
                        logger.warning("Gemini model %s is out of quota (HTTP 429 %s): %s", model, status_name, message)
                        last_exc = LLMQuotaExceededError()
                        break
                    if exc.code >= 500:
                        logger.warning(
                            "Gemini model %s is unavailable (HTTP %s %s): %s", model, exc.code, status_name, message
                        )
                        last_exc = LLMServiceError()
                        if not is_last_model:
                            # Fail over at once instead of retrying: an overloaded
                            # model is rarely back within seconds, and another one
                            # usually answers now. Measured in the outage this was
                            # written for, retrying the first model twice added ~7s
                            # before failing over anyway. Only the last model left
                            # is worth retrying.
                            break
                        attempt += 1
                        if attempt < attempts:
                            time.sleep(min(2 ** (attempt - 1), 4))
                        continue
                    # Any other 4xx describes the request itself, and every
                    # other model would reject it the same way.
                    logger.error("Gemini request to %s failed (HTTP %s %s): %s", model, exc.code, status_name, message)
                    raise LLMServiceError() from exc
                except (socket.timeout, TimeoutError) as exc:
                    attempt += 1
                    if attempt < attempts:
                        continue
                    # A request that ran to the full timeout would do the same
                    # on the next model; stopping bounds the total wait.
                    raise LLMTimeoutError() from exc
                except (URLError, ConnectionError) as exc:
                    logger.warning("Could not reach Gemini: %s", exc)
                    attempt += 1
                    if attempt < attempts:
                        time.sleep(1)
                        continue
                    # The network is down for every model, not only this one.
                    raise LLMServiceError() from exc
                else:
                    result = self._parse_generate_body(body)
                    if retried_without_thinking:
                        logger.info("Gemini model %s rejects thinkingBudget=0; it will be sent without it.", model)
                        _remember_thinking_budget_rejected(model)
                    self.last_model_used = model
                    if index:
                        logger.warning(
                            "Served by fallback Gemini model %s (first choice: %s).", model, candidates[0]
                        )
                    return result

        raise last_exc or LLMModelUnavailableError()

    def probe_model(self, model):
        """One tiny real generation against exactly ``model``, for the
        ``check_ai`` command. Returns ``(ok, detail)`` and never raises for a
        provider error - a diagnostic that stops at the first failing model
        says nothing about the models after it."""
        schema = _to_gemini_schema(_PROBE_SCHEMA)
        send_thinking = not _model_rejects_thinking_budget(model)
        started = time.monotonic()

        for _ in range(2):
            try:
                body = self._post_generate(model, _PROBE_PROMPT, schema, send_thinking, max_output_tokens=64)
                self._parse_generate_body(body)
                if not send_thinking:
                    _remember_thinking_budget_rejected(model)
                return True, f"answered in {time.monotonic() - started:.1f}s"
            except HTTPError as exc:
                status_name, message, _ = _google_error_details(exc)
                if exc.code == 400 and send_thinking:
                    send_thinking = False
                    continue
                return False, f"HTTP {exc.code} {status_name} - {message}".strip(" -")
            except LLMServiceError as exc:
                return False, exc.code
            except (socket.timeout, TimeoutError):
                return False, "timed out"
            except (URLError, ConnectionError) as exc:
                return False, f"network error: {exc}"
        return False, "rejected the request"

    def check_model_available(self):
        """Readiness: the key is accepted and at least one usable model is
        offered to it. Spends no generation call."""
        if not self.api_key:
            logger.error("No Gemini API key is configured - add GEMINI_API_KEY in the admin Configuration tab.")
            raise LLMAuthError()

        if self.discover_models:
            available = self.available_models()
            if available:
                if not self.candidate_models():
                    raise LLMModelUnavailableError()
                return True

        # Discovery is off or the list was unreachable: probe each configured
        # model's metadata directly instead.
        last_exc = None
        for model in self.candidate_models():
            request = Request(
                f"{self.base_url}/models/{quote(model, safe='-._')}",
                headers={"x-goog-api-key": self.api_key, "User-Agent": "IntelliResume/1.0"},
                method="GET",
            )
            try:
                with urlopen(request, timeout=min(self.timeout, 10)) as response:
                    response.read()
                return True
            except HTTPError as exc:
                status_name, message, reasons = _google_error_details(exc)
                if _is_key_rejection(exc.code, message, reasons):
                    raise LLMAuthError() from exc
                if exc.code == 404:
                    last_exc = LLMModelUnavailableError()
                    continue
                raise LLMServiceError() from exc
            except (socket.timeout, TimeoutError) as exc:
                raise LLMTimeoutError() from exc
            except (URLError, ConnectionError) as exc:
                raise LLMServiceError() from exc

        raise last_exc or LLMModelUnavailableError()

    # -- wire format ------------------------------------------------------------

    def _post_generate(self, model, prompt, response_schema, send_thinking, max_output_tokens=None):
        generation_config = {
            "temperature": self.temperature,
            "responseMimeType": "application/json",
            "responseSchema": response_schema,
            "maxOutputTokens": max_output_tokens or self.max_output_tokens,
        }
        if send_thinking:
            # Thinking tokens are slow and billed, and schema-constrained
            # extraction gains nothing from them.
            generation_config["thinkingConfig"] = {"thinkingBudget": 0}

        payload = json.dumps({
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }).encode("utf-8")
        request = Request(
            f"{self.base_url}/models/{quote(model, safe='-._')}:generateContent",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": self.api_key,
                "User-Agent": "IntelliResume/1.0",
            },
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            raw = response.read().decode("utf-8")
        try:
            return json.loads(raw)
        except ValueError as exc:
            raise LLMInvalidResponseError() from exc

    @staticmethod
    def _parse_generate_body(body):
        candidates = body.get("candidates") if isinstance(body, dict) else None
        if not isinstance(candidates, list) or not candidates:
            raise LLMEmptyResponseError()
        parts = (candidates[0].get("content") or {}).get("parts") or []
        content = "".join(
            part.get("text", "")
            for part in parts
            if isinstance(part, dict) and not part.get("thought", False)
        )
        if not content.strip():
            raise LLMEmptyResponseError()
        try:
            return json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMInvalidResponseError() from exc


class FallbackLLMService(LLMService):
    """Wraps a primary provider client with a fallback: if the primary is
    unreachable, times out, or has no model configured, retries the exact
    same call against the fallback provider once before giving up.
    Transparent to every caller — same LLMService interface as any single
    provider (COMMERCIAL_READINESS_AUDIT.md A5).

    Deliberately does NOT fall back on LLMSchemaValidationError or
    LLMInvalidResponseError: those mean the provider responded and the
    *content* didn't pass validation, which is what each AI service's own
    grounding/retry-with-a-stricter-prompt logic already exists to handle
    — conflating the two would mask real prompt bugs behind a provider
    switch instead of surfacing them.
    """

    def __init__(self, primary, fallback, fallback_provider_name):
        self.primary = primary
        self.fallback = fallback
        self.fallback_provider_name = fallback_provider_name
        self.used_fallback = False

    def generate_structured(self, prompt, schema):
        try:
            return self.primary.generate_structured(prompt, schema)
        except (LLMSchemaValidationError, LLMInvalidResponseError):
            raise
        except LLMServiceError as exc:
            logger.warning(
                "Primary AI provider failed (%s); retrying once with fallback provider %s.",
                exc.code, self.fallback_provider_name,
            )
            self.used_fallback = True
            return self.fallback.generate_structured(prompt, schema)

    def check_model_available(self):
        try:
            return self.primary.check_model_available()
        except (LLMSchemaValidationError, LLMInvalidResponseError):
            raise
        except LLMServiceError:
            return self.fallback.check_model_available()


def resolve_provider(provider=None):
    """The same fallback ``get_llm_service`` applies, exposed standalone so a
    view can know *which* provider will actually be used — e.g. to record
    accurate usage/cost tracking — without needing to construct a client."""
    provider_default = get_config_value("LLM_PROVIDER", settings.LLM_PROVIDER)
    return (provider or provider_default or "ollama").lower()


def _provider_api_key(setting_name):
    """A provider API key: the admin Configuration tab first (stored
    encrypted; get_config_value decrypts it), then the server environment.

    Stripped, because a key pasted with a trailing space or newline is
    rejected by every provider - and "I pasted the new key and it still
    doesn't work" is exactly the failure this must not produce.
    """
    return str(get_config_value(setting_name, getattr(settings, setting_name, "")) or "").strip()


def _build_client(provider, timeout, max_retries, temperature, max_output_tokens):
    if provider == "groq":
        return GroqLLMService(
            api_key=_provider_api_key("GROQ_API_KEY"),
            model=get_config_value("GROQ_MODEL", settings.GROQ_MODEL),
            timeout=timeout,
            max_retries=max_retries,
            temperature=temperature,
            base_url=get_config_value("GROQ_BASE_URL", settings.GROQ_BASE_URL),
            max_output_tokens=max_output_tokens,
        )

    if provider == "gemini":
        return GeminiLLMService(
            api_key=_provider_api_key("GEMINI_API_KEY"),
            model=get_config_value("GEMINI_MODEL", settings.GEMINI_MODEL),
            timeout=timeout,
            max_retries=max_retries,
            temperature=temperature,
            base_url=get_config_value("GEMINI_BASE_URL", settings.GEMINI_BASE_URL),
            max_output_tokens=max_output_tokens,
            fallback_models=get_config_value("GEMINI_FALLBACK_MODELS", settings.GEMINI_FALLBACK_MODELS),
            discover_models=get_config_value("GEMINI_MODEL_DISCOVERY", settings.GEMINI_MODEL_DISCOVERY),
            auto_families=get_config_value(
                "GEMINI_AUTO_FALLBACK_FAMILIES", settings.GEMINI_AUTO_FALLBACK_FAMILIES
            ),
            auto_exclude=get_config_value("GEMINI_AUTO_EXCLUDE", settings.GEMINI_AUTO_EXCLUDE),
            max_model_attempts=get_config_value("GEMINI_MAX_MODEL_ATTEMPTS", settings.GEMINI_MAX_MODEL_ATTEMPTS),
            model_list_ttl=get_config_value("GEMINI_MODEL_LIST_TTL", settings.GEMINI_MODEL_LIST_TTL),
        )

    if provider == "kira":
        return KiraLLMService(
            api_key=_provider_api_key("KIRA_API_KEY"),
            model=get_config_value("KIRA_MODEL", settings.KIRA_MODEL),
            timeout=timeout,
            max_retries=max_retries,
            temperature=temperature,
            base_url=get_config_value("KIRA_BASE_URL", settings.KIRA_BASE_URL),
            max_output_tokens=max_output_tokens,
        )

    return OllamaLLMService(
        base_url=get_config_value("OLLAMA_BASE_URL", settings.OLLAMA_BASE_URL),
        model=get_config_value("OLLAMA_MODEL", settings.OLLAMA_MODEL),
        timeout=timeout,
        max_retries=max_retries,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        keep_alive=get_config_value("OLLAMA_KEEP_ALIVE", settings.OLLAMA_KEEP_ALIVE),
    )


def get_llm_service(provider=None):
    """Build the requested provider's client without exposing it to controllers.

    ``provider`` lets a single request opt into a specific AI provider
    (e.g. a user's saved preference); it falls back to the server-wide
    ``LLM_PROVIDER`` setting when not supplied.

    When ``provider`` was NOT explicitly requested (the common case — most
    calls just use the server default) and ``LLM_FALLBACK_PROVIDER`` is
    configured, the returned client is wrapped so a primary-provider
    outage automatically retries once against the fallback provider
    (COMMERCIAL_READINESS_AUDIT.md A5) rather than failing the request
    outright. An explicitly requested provider is never silently swapped
    out from under the caller — that would bypass their plan's
    allowed-provider gate and surprise a user who specifically chose it.
    """
    explicit_request = provider is not None
    resolved_provider = resolve_provider(provider)
    if resolved_provider not in SUPPORTED_PROVIDERS:
        raise LLMServiceError("Unsupported AI provider.")

    timeout = get_config_value("LLM_TIMEOUT", settings.LLM_TIMEOUT)
    max_retries = get_config_value("LLM_MAX_RETRIES", settings.LLM_MAX_RETRIES)
    temperature = get_config_value("LLM_TEMPERATURE", settings.LLM_TEMPERATURE)
    max_output_tokens = get_config_value("LLM_MAX_OUTPUT_TOKENS", settings.LLM_MAX_OUTPUT_TOKENS)

    primary = _build_client(resolved_provider, timeout, max_retries, temperature, max_output_tokens)

    fallback_provider = get_config_value("LLM_FALLBACK_PROVIDER", settings.LLM_FALLBACK_PROVIDER)
    if (
        not explicit_request
        and fallback_provider
        and fallback_provider != resolved_provider
        and fallback_provider in SUPPORTED_PROVIDERS
    ):
        fallback_client = _build_client(fallback_provider, timeout, max_retries, temperature, max_output_tokens)
        return FallbackLLMService(primary, fallback_client, fallback_provider)

    return primary
