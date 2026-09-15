"""Check the AI provider configuration with real calls.

    python manage.py check_ai                  # the configured default provider
    python manage.py check_ai --provider groq
    python manage.py check_ai --no-generate    # spends no quota

Built for the moment right after a key or model change, when the question is
"is it actually working now?" and the logs can't answer it. It shows where
each setting really comes from (the admin panel's database value, or .env),
whether the provider accepts the key, which models the key is offered, and
makes one real, tiny generation against each model in the fallback chain.

Key values are never printed - only whether one is set, its length, and where
it comes from. Exits non-zero when the provider cannot generate, so a deploy
can gate on it.
"""

import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError

from api.models import SystemConfiguration
from api.utils.config import get_config_value
from api.utils.secrets import SecretDecryptionError, decrypt_value
from resumes.services.llm_service import (
    SUPPORTED_PROVIDERS,
    GeminiLLMService,
    LLMServiceError,
    get_llm_service,
    resolve_provider,
)

CHECK_PROMPT = 'Reply with the JSON object {"ok": true} and nothing else.'
CHECK_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}

CONFIG_KEYS = (
    "LLM_PROVIDER",
    "LLM_FALLBACK_PROVIDER",
    "GEMINI_MODEL",
    "GEMINI_FALLBACK_MODELS",
    "GEMINI_MODEL_DISCOVERY",
    "GEMINI_AUTO_FALLBACK_FAMILIES",
    "GEMINI_AUTO_EXCLUDE",
    "GEMINI_MAX_MODEL_ATTEMPTS",
    "GROQ_MODEL",
    "KIRA_MODEL",
    "OLLAMA_MODEL",
    "LLM_TIMEOUT",
    "LLM_MAX_RETRIES",
)
KEY_SETTINGS = ("GEMINI_API_KEY", "GROQ_API_KEY", "KIRA_API_KEY")
NEW_GEMINI_KEY_ADVICE = (
    "Create a key at https://aistudio.google.com/apikey and paste it into GEMINI_API_KEY in the admin "
    "Configuration tab. It takes effect on the next request - no restart needed."
)


class Command(BaseCommand):
    help = "Check the AI provider configuration with real calls."

    def add_arguments(self, parser):
        parser.add_argument(
            "--provider",
            choices=SUPPORTED_PROVIDERS,
            help="Check this provider instead of the configured default.",
        )
        parser.add_argument(
            "--no-generate",
            action="store_true",
            help="Skip the real generation calls, so no quota is spent.",
        )

    def handle(self, *args, **options):
        provider = options["provider"] or resolve_provider()
        generate = not options["no_generate"]

        self._heading("Configuration in effect")
        self._print_configuration()
        self._heading("API keys (values are never shown)")
        self._print_keys()
        self._heading(f"Provider check: {provider}")

        ok = self._check_gemini(generate) if provider == "gemini" else self._check_generic(provider, generate)

        fallback = get_config_value("LLM_FALLBACK_PROVIDER", settings.LLM_FALLBACK_PROVIDER)
        if not options["provider"] and fallback and fallback != provider:
            self.stdout.write(
                f"\nFallback provider '{fallback}' is also configured. "
                f"Check it with: python manage.py check_ai --provider {fallback}"
            )

        if not ok:
            raise CommandError(f"The '{provider}' AI provider is not working - see the checks above.")
        self.stdout.write(self.style.SUCCESS(f"\nOK: '{provider}' is working."))

    # -- sections -----------------------------------------------------------

    def _heading(self, title):
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n{title}"))

    def _rows(self, keys):
        try:
            return {row.key: row for row in SystemConfiguration.objects.filter(key__in=keys)}
        except DatabaseError:
            self.stdout.write(self.style.WARNING("  Could not read the database - showing .env values only."))
            return {}

    def _print_configuration(self):
        rows = self._rows(CONFIG_KEYS)
        for key in CONFIG_KEYS:
            if key in rows:
                value, source = rows[key].parsed_value(), "admin panel (database)"
            else:
                value, source = getattr(settings, key, ""), ".env / default - no admin panel value yet"
            shown = ",".join(value) if isinstance(value, (list, tuple)) else str(value)
            self.stdout.write(f"  {key:<30} {shown or '(empty)':<34} <- {source}")

    def _print_keys(self):
        rows = self._rows(KEY_SETTINGS)
        for setting in KEY_SETTINGS:
            if setting in rows:
                try:
                    value = str(decrypt_value(rows[setting].value) or "").strip()
                except SecretDecryptionError:
                    self.stdout.write(self.style.ERROR(
                        f"  {setting:<16} stored in the admin panel but cannot be decrypted "
                        f"(CONFIG_ENCRYPTION_KEY or SECRET_KEY changed) - the .env key is being used; "
                        f"re-enter it in the admin Configuration tab."
                    ))
                    continue
                source = "admin panel (encrypted)"
            else:
                value, source = str(getattr(settings, setting, "") or "").strip(), ".env"
            state = f"set, {len(value)} characters <- {source}" if value else "not set"
            self.stdout.write(f"  {setting:<16} {state}")

    def _check_gemini(self, generate):
        service = get_llm_service(provider="gemini")
        if not isinstance(service, GeminiLLMService):
            return self._check_generic("gemini", generate)

        if not service.api_key:
            self.stdout.write(self.style.ERROR("  No Gemini API key is configured."))
            self.stdout.write(f"  {NEW_GEMINI_KEY_ADVICE}")
            return False

        try:
            available = service.available_models()
        except LLMServiceError as exc:
            self.stdout.write(self.style.ERROR(f"  Google rejected the API key ({exc.code})."))
            self.stdout.write(f"  {NEW_GEMINI_KEY_ADVICE}")
            return False

        if available is None:
            self.stdout.write(self.style.WARNING(
                "  Could not fetch the model list (network trouble, or GEMINI_MODEL_DISCOVERY is off). "
                "Only the configured models will be tried."
            ))
        else:
            self.stdout.write(f"  Key accepted. {len(available)} models can generate content with it.")
            for model in dict.fromkeys(m for m in [service.model, *service.fallback_models] if m):
                if model in available:
                    self.stdout.write(f"  configured {model}: offered")
                else:
                    self.stdout.write(self.style.WARNING(
                        f"  configured {model}: NOT offered to this key - discontinued or not enabled, "
                        f"so it is skipped. Update it in the admin Configuration tab."
                    ))

        chain = service.candidate_models()
        self.stdout.write(f"  Models are tried in this order: {' -> '.join(chain) or '(none)'}")
        if not chain:
            self.stdout.write(
                "  Nothing to try. Set GEMINI_MODEL in the admin Configuration tab, or turn on "
                "GEMINI_MODEL_DISCOVERY with GEMINI_AUTO_FALLBACK_FAMILIES set."
            )
            return False
        if not generate:
            return True

        working = []
        for model in chain:
            ok, detail = service.probe_model(model)
            if ok:
                working.append(model)
                self.stdout.write(self.style.SUCCESS(f"  [ok]   {model}: {detail}"))
            else:
                self.stdout.write(self.style.ERROR(f"  [fail] {model}: {detail}"))

        if not working:
            self.stdout.write(
                "  No model answered. 503 means Google is overloaded (usually temporary); 429 means this "
                "key's quota is used up; 404 means the model is gone - pick one listed as offered and set "
                "it in the admin Configuration tab."
            )
            return False
        if working[0] != chain[0]:
            self.stdout.write(self.style.WARNING(
                f"  {chain[0]} is not answering right now; requests are served by {working[0]} until it does."
            ))
        return True

    def _check_generic(self, provider, generate):
        service = get_llm_service(provider=provider)
        try:
            service.check_model_available()
            self.stdout.write("  Provider reachable, and the configured model is available.")
            if generate:
                started = time.monotonic()
                service.generate_structured(CHECK_PROMPT, CHECK_SCHEMA)
                self.stdout.write(self.style.SUCCESS(
                    f"  [ok]   generation answered in {time.monotonic() - started:.1f}s"
                ))
            return True
        except LLMServiceError as exc:
            self.stdout.write(self.style.ERROR(f"  [fail] {exc.code}: {exc}"))
            return False
