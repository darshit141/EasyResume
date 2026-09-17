"""Bring-your-own provider keys: storage, validation and lookup.

Every rule about how a user's key is handled lives here, so there is exactly
one place to audit. Nothing outside this module decrypts a key, and nothing
outside this module decides whether BYOK is switched on.
"""

import logging

from django.utils import timezone

from api.utils.config import get_config_value
from api.utils.secrets import (
    DOMAIN_USER_PROVIDER_KEY,
    SecretDecryptionError,
    decrypt_value,
    encrypt_value,
)
from billing.models import UserAIKey

logger = logging.getLogger(__name__)

# Long enough to reject an obvious paste accident (an empty box, a stray
# word) without pretending to know a provider's key format — that is the
# provider's job, and we ask them directly in `validate`.
_MIN_KEY_LENGTH = 20
_MAX_KEY_LENGTH = 400


class UserAIKeyError(Exception):
    """A user-facing problem with a supplied key."""

    code = "INVALID_API_KEY"

    def __init__(self, message, code=None):
        self.message = message
        if code:
            self.code = code
        super().__init__(message)


class BYOKDisabledError(UserAIKeyError):
    code = "BYOK_DISABLED"


class UserAIKeyService:
    @staticmethod
    def enabled():
        """Whether users may supply their own keys at all.

        Read through the admin Configuration tab rather than an env var so
        it can be switched off immediately — if the feature ever turns out
        to be a support burden or a liability, an operator should not need
        a deploy to stop new keys being accepted.
        """
        return bool(get_config_value("BYOK_ENABLED", False))

    @staticmethod
    def supported_providers():
        return [choice[0] for choice in UserAIKey.PROVIDER_CHOICES]

    @staticmethod
    def _require_enabled():
        if not UserAIKeyService.enabled():
            raise BYOKDisabledError(
                "Using your own AI provider key is not available on this server."
            )

    @staticmethod
    def validate(provider, api_key):
        """Ask the provider whether the key works, before we store it.

        Doing this at save time is the whole difference between a feature
        that feels reliable and one that doesn't: the alternative is a user
        pasting a key with a trailing character, seeing "saved", and then
        hitting an unexplained AI failure three screens later with no idea
        the two are connected.
        """
        from resumes.services.llm_service import (
            LLMAuthError,
            LLMServiceError,
            build_client_with_key,
        )

        client = build_client_with_key(provider, api_key)
        try:
            client.check_model_available()
        except LLMAuthError as exc:
            raise UserAIKeyError(
                "That key was rejected by the provider. Check you copied it in "
                "full, and that the API is enabled for it.",
                code="INVALID_API_KEY",
            ) from exc
        except LLMServiceError as exc:
            # A reachable provider that is merely busy, or a model that is
            # temporarily unavailable, says nothing about the key. Refusing
            # to save it would be wrong, so this is reported as a transient
            # failure and the user is invited to try again.
            raise UserAIKeyError(
                "We couldn't reach the provider to check that key just now. "
                "Please try again in a moment.",
                code="PROVIDER_UNREACHABLE",
            ) from exc

    @staticmethod
    def set_key(user, provider, api_key):
        """Validate, then store encrypted. Replaces any existing key for
        the same provider."""
        UserAIKeyService._require_enabled()

        if provider not in UserAIKeyService.supported_providers():
            raise UserAIKeyError(
                f"'{provider}' is not a provider you can supply your own key for.",
                code="UNSUPPORTED_PROVIDER",
            )

        # Stripped for the same reason the server-wide keys are: a key
        # pasted with a trailing newline is rejected by every provider, and
        # "I pasted the new key and it still doesn't work" is exactly the
        # failure this must not produce.
        api_key = (api_key or "").strip()
        if not (_MIN_KEY_LENGTH <= len(api_key) <= _MAX_KEY_LENGTH):
            raise UserAIKeyError("That doesn't look like a complete API key.")

        UserAIKeyService.validate(provider, api_key)

        record, _ = UserAIKey.objects.update_or_create(
            user=user,
            provider=provider,
            defaults={
                "encrypted_key": encrypt_value(api_key, domain=DOMAIN_USER_PROVIDER_KEY),
                "last_four": api_key[-4:],
                "is_active": True,
                "last_validated_at": timezone.now(),
                "last_error_code": "",
            },
        )
        # Note the absence of the key, the user's email, or any part of the
        # key beyond what is already stored — this line goes to the same log
        # everything else does.
        logger.info("Stored a user-supplied %s key for user_id=%s.", provider, user.id)
        return record

    @staticmethod
    def clear_key(user, provider):
        """Remove a stored key entirely. Deleting rather than deactivating:
        when a user asks for their credential to be gone, it goes."""
        deleted, _ = UserAIKey.objects.filter(user=user, provider=provider).delete()
        return bool(deleted)

    @staticmethod
    def set_active(user, provider, is_active):
        record = UserAIKey.objects.filter(user=user, provider=provider).first()
        if record is None:
            raise UserAIKeyError("No key is saved for that provider.", code="NO_KEY")
        record.is_active = is_active
        record.save(update_fields=["is_active", "updated_at"])
        return record

    @staticmethod
    def active_key_for(user, provider):
        """The usable plaintext key for this user and provider, or None.

        Returns None — rather than raising — for every "no key in play"
        case, including a key that can no longer be decrypted, because the
        caller's correct response to all of them is identical: fall back to
        the server's own credentials, which is what would have happened if
        the user had never saved a key at all.
        """
        if user is None or not getattr(user, "is_authenticated", False):
            return None
        if not UserAIKeyService.enabled():
            return None

        record = UserAIKey.objects.filter(
            user=user, provider=provider, is_active=True
        ).first()
        if record is None:
            return None

        try:
            return decrypt_value(record.encrypted_key, domain=DOMAIN_USER_PROVIDER_KEY)
        except SecretDecryptionError:
            logger.error(
                "User-supplied %s key for user_id=%s could not be decrypted "
                "(CONFIG_ENCRYPTION_KEY changed?) - falling back to the server key.",
                provider, user.id,
            )
            return None

    @staticmethod
    def has_active_key(user, provider=None):
        """Whether this user is currently covering their own AI cost.

        Pass the specific provider a call will actually be served by — a
        key for one provider must not be read as "somebody else is paying"
        for a request that will run on a *different* provider's server
        credentials. Only entitlements_service.usage_summary(), which is
        describing the account rather than gating one specific call, asks
        with no provider at all.
        """
        if user is None or not getattr(user, "is_authenticated", False):
            return False
        if not UserAIKeyService.enabled():
            return False

        query = UserAIKey.objects.filter(user=user, is_active=True)
        if provider:
            query = query.filter(provider=provider)
        return query.exists()

    @staticmethod
    def mark_used(user, provider):
        """Best-effort 'last used' stamp. Never allowed to fail a real AI
        call: this is a nicety on a settings screen, not a ledger."""
        try:
            UserAIKey.objects.filter(user=user, provider=provider).update(
                last_used_at=timezone.now()
            )
        except Exception:  # pragma: no cover - defensive
            logger.debug("Could not stamp last_used_at for a user AI key.", exc_info=True)

    @staticmethod
    def summary(user):
        """What the settings screen shows. Never contains a key."""
        records = {
            record.provider: record
            for record in UserAIKey.objects.filter(user=user)
        }
        return {
            "enabled": UserAIKeyService.enabled(),
            "supported_providers": UserAIKeyService.supported_providers(),
            "keys": [
                {
                    "provider": provider,
                    "label": label,
                    "configured": provider in records,
                    "is_active": records[provider].is_active if provider in records else False,
                    "masked_key": f"••••••••{records[provider].last_four}" if provider in records else "",
                    "last_validated_at": (
                        records[provider].last_validated_at if provider in records else None
                    ),
                    "last_used_at": records[provider].last_used_at if provider in records else None,
                }
                for provider, label in UserAIKey.PROVIDER_CHOICES
            ],
        }
