"""Provider-neutral structured-generation interface and local Ollama adapter."""

from abc import ABC, abstractmethod
import json
import socket
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings


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

    def __init__(self, base_url, model, timeout, max_retries, temperature):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
        self.temperature = temperature

    def generate_structured(self, prompt, schema):
        if not self.model:
            raise LLMModelUnavailableError()

        payload = json.dumps({
            "model": self.model,
            "prompt": prompt,
            "format": schema,
            "stream": False,
            "options": {"temperature": self.temperature},
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


def get_llm_service():
    """Build the configured provider without exposing it to controllers."""
    if settings.LLM_PROVIDER.lower() != "ollama":
        raise LLMServiceError("Unsupported local LLM provider.")
    return OllamaLLMService(
        base_url=settings.OLLAMA_BASE_URL,
        model=settings.OLLAMA_MODEL,
        timeout=settings.LLM_TIMEOUT,
        max_retries=settings.LLM_MAX_RETRIES,
        temperature=settings.LLM_TEMPERATURE,
    )
