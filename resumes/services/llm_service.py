"""Provider-neutral structured-generation interface and local Ollama adapter."""

from abc import ABC, abstractmethod
import json
import logging
import socket
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
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
                if exc.code in (401, 403, 404):
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
            if exc.code in (401, 403, 404):
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


class GeminiLLMService(LLMService):
    """Client for Google Gemini's generateContent API, using its native
    ``responseSchema`` support for real schema-constrained JSON output."""

    DEFAULT_FALLBACK_MODELS = (
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-flash-latest",
        "gemini-3.5-flash",
        "gemini-3.7-flash",
    )

    def __init__(self, api_key, model, timeout, max_retries, temperature, base_url, max_output_tokens=8192):
        self.api_key = api_key
        self.model = model or "gemini-3.8-flash"
        self.timeout = timeout
        self.max_retries = max_retries
        self.temperature = temperature
        self.base_url = base_url.rstrip("/")
        self.max_output_tokens = max_output_tokens

    def generate_structured(self, prompt, schema):
        if not self.model or not self.api_key:
            raise LLMModelUnavailableError()

        candidates = [self.model]
        for fb in self.DEFAULT_FALLBACK_MODELS:
            if fb not in candidates:
                candidates.append(fb)

        last_exc = None

        for model_idx, model in enumerate(candidates):
            generation_config = {
                "temperature": self.temperature,
                "responseMimeType": "application/json",
                "responseSchema": _to_gemini_schema(schema),
                "maxOutputTokens": self.max_output_tokens,
            }

            model_lower = model.lower()
            if "lite" not in model_lower and "1.5" not in model_lower:
                generation_config["thinkingConfig"] = {
                    "thinkingBudget": 0
                }

            payload = json.dumps({
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": generation_config,
            }).encode("utf-8")
            request = Request(
                f"{self.base_url}/models/{model}:generateContent",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "x-goog-api-key": self.api_key,
                    "User-Agent": "IntelliResume/1.0",
                },
                method="POST",
            )

            attempts = self.max_retries + 1 if model_idx == 0 else 1
            for attempt in range(attempts):
                try:
                    with urlopen(request, timeout=self.timeout) as response:
                        body = json.loads(response.read().decode("utf-8"))
                    candidates_data = body.get("candidates")
                    if not isinstance(candidates_data, list) or not candidates_data:
                        raise LLMEmptyResponseError()
                    parts = (candidates_data[0].get("content") or {}).get("parts") or []
                    content = "".join(
                        part.get("text", "") for part in parts if isinstance(part, dict) and not part.get("thought", False)
                    )
                    if not content.strip():
                        raise LLMEmptyResponseError()
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError as exc:
                        raise LLMInvalidResponseError() from exc
                except HTTPError as exc:
                    if exc.code in (401, 403):
                        raise LLMModelUnavailableError() from exc
                    if exc.code == 404:
                        logger.warning("Gemini model %s returned 404 (model retired/unavailable), trying fallback model.", model)
                        last_exc = LLMModelUnavailableError()
                        break
                    if exc.code in (503, 429):
                        logger.warning("Gemini model %s returned HTTP %s (busy/rate limit), retrying...", model, exc.code)
                        last_exc = LLMServiceError()
                        if attempt < attempts - 1:
                            time.sleep(1)
                            continue
                        break
                    raise LLMServiceError() from exc
                except (socket.timeout, TimeoutError) as exc:
                    last_exc = LLMTimeoutError()
                    if attempt < attempts - 1:
                        time.sleep(1)
                        continue
                    break
                except (URLError, ConnectionError) as exc:
                    last_exc = LLMServiceError(f"Network error: {exc}")
                    if attempt < attempts - 1:
                        time.sleep(1)
                        continue
                    break

        if last_exc:
            raise last_exc
        raise LLMServiceError()

    def check_model_available(self):
        if not self.model or not self.api_key:
            raise LLMModelUnavailableError()

        candidates = [self.model]
        for fb in self.DEFAULT_FALLBACK_MODELS:
            if fb not in candidates:
                candidates.append(fb)

        last_exc = None
        for model in candidates:
            request = Request(
                f"{self.base_url}/models/{model}",
                headers={
                    "x-goog-api-key": self.api_key,
                    "User-Agent": "IntelliResume/1.0",
                },
                method="GET",
            )
            try:
                with urlopen(request, timeout=min(self.timeout, 10)) as response:
                    response.read()
                return True
            except HTTPError as exc:
                if exc.code in (401, 403):
                    raise LLMModelUnavailableError() from exc
                if exc.code == 404:
                    last_exc = LLMModelUnavailableError()
                    continue
                raise LLMServiceError() from exc
            except (socket.timeout, TimeoutError) as exc:
                raise LLMTimeoutError() from exc
            except (URLError, ConnectionError) as exc:
                raise LLMServiceError() from exc

        if last_exc:
            raise last_exc
        return False


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


def _build_client(provider, timeout, max_retries, temperature, max_output_tokens):
    if provider == "groq":
        return GroqLLMService(
            api_key=get_config_value("GROQ_API_KEY", settings.GROQ_API_KEY),
            model=get_config_value("GROQ_MODEL", settings.GROQ_MODEL),
            timeout=timeout,
            max_retries=max_retries,
            temperature=temperature,
            base_url=get_config_value("GROQ_BASE_URL", settings.GROQ_BASE_URL),
            max_output_tokens=max_output_tokens,
        )

    if provider == "gemini":
        return GeminiLLMService(
            api_key=get_config_value("GEMINI_API_KEY", settings.GEMINI_API_KEY),
            model=get_config_value("GEMINI_MODEL", settings.GEMINI_MODEL),
            timeout=timeout,
            max_retries=max_retries,
            temperature=temperature,
            base_url=get_config_value("GEMINI_BASE_URL", settings.GEMINI_BASE_URL),
            max_output_tokens=max_output_tokens,
        )

    if provider == "kira":
        return KiraLLMService(
            api_key=get_config_value("KIRA_API_KEY", settings.KIRA_API_KEY),
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
