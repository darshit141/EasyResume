"""Pluggable rewarded-video ad providers.

Same shape as ``resumes.services.llm_service``: one small abstract
interface, a concrete adapter per provider, and a resolver that returns the
one an operator selected in the admin Configuration tab
(``REWARDED_AD_PROVIDER``). Two real providers ship — Google Ad Manager and
a generic "partner" adapter for any network whose server-to-server reward
postback is an HMAC-signed callback — plus a null provider used whenever the
feature is switched off or left unconfigured.

Nothing here credits an account by itself. Each provider only *verifies*
that a reward callback genuinely came from the ad network and says which of
our users earned how much; the actual grant is written by
``CreditService.grant_rewarded_ad_credit`` so the daily cap, expiry, and
idempotency live in one place regardless of provider.

Secrets and network ids are read from the environment, never from
``SystemConfiguration`` — the same deliberate choice made for the LLM
provider keys (a DB row is plaintext in every backup).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from urllib.request import urlopen

from django.conf import settings
from django.core import signing

from api.utils.config import get_config_value

logger = logging.getLogger(__name__)

# The signed ticket handed to the browser before it shows an ad and echoed
# back by the network in its reward callback, so the callback can be tied to
# the real user who watched. Short-lived: a ticket is meant to be redeemed
# within one ad view.
_TICKET_SALT = "billing.rewarded_ad.ticket"
TICKET_TTL_SECONDS = 15 * 60


class RewardedAdError(Exception):
    """Base for rewarded-ad failures that should surface to a caller."""


class RewardedAdVerificationError(RewardedAdError):
    """The reward callback could not be trusted — bad signature, replayed
    timestamp, unknown key, malformed payload. Always answered with 403 and
    never credits anything."""


class RewardedAdNotConfiguredError(RewardedAdError):
    """The selected provider is missing the environment it needs to run."""


@dataclass(frozen=True)
class RewardedAdReward:
    """The trustworthy result of verifying a reward callback."""

    user_id: int
    amount: int
    external_id: str
    provider: str


def issue_ticket(user_id: int) -> str:
    """A signed, timestamped token binding an upcoming ad view to a user."""
    return signing.dumps({"uid": int(user_id)}, salt=_TICKET_SALT)


def read_ticket(raw: str) -> int:
    try:
        data = signing.loads(raw, salt=_TICKET_SALT, max_age=TICKET_TTL_SECONDS)
    except signing.SignatureExpired as exc:
        raise RewardedAdVerificationError("Reward ticket has expired.") from exc
    except signing.BadSignature as exc:
        raise RewardedAdVerificationError("Reward ticket is invalid.") from exc
    uid = data.get("uid")
    if not isinstance(uid, int):
        raise RewardedAdVerificationError("Reward ticket is malformed.")
    return uid


class RewardedAdProvider:
    """The interface a view depends on. Never instantiated directly — use
    :func:`get_rewarded_ad_provider`."""

    name = "none"

    def is_configured(self) -> bool:
        raise NotImplementedError

    def client_config(self) -> dict:
        """Non-secret settings the browser SDK needs (network code, unit
        path, script url…). Safe to return in an authenticated API
        response; must never contain a key or secret."""
        raise NotImplementedError

    def verify_callback(self, *, method: str, params: dict, body: bytes) -> RewardedAdReward:
        """Validate a server-to-server reward callback and return who to
        credit. Raise :class:`RewardedAdVerificationError` on anything that
        doesn't check out."""
        raise NotImplementedError


class NullRewardedAdProvider(RewardedAdProvider):
    """Feature off, or no provider selected. Everything is a no-op and the
    client is told the feature is unavailable."""

    name = "none"

    def is_configured(self) -> bool:
        return False

    def client_config(self) -> dict:
        return {}

    def verify_callback(self, *, method, params, body):
        raise RewardedAdNotConfiguredError("Rewarded ads are not enabled.")


class AdManagerRewardedAdProvider(RewardedAdProvider):
    """Google Ad Manager / AdMob rewarded ads with server-side verification.

    The browser shows a rewarded slot via GPT; when the view completes
    Google calls our callback URL (a GET) with the reward plus an ECDSA
    signature over the query string. We verify it against Google's published
    verifier keys, exactly as documented for AdMob SSV. ``user_id`` in that
    callback is the signed ticket we handed the page, so it resolves back to
    a real account.
    """

    name = "admanager"
    VERIFIER_KEYS_URL = "https://www.gstatic.com/admob/reward/verifier-keys.json"
    _keys_cache: dict | None = None
    _keys_fetched_at: float = 0.0
    _KEYS_TTL = 6 * 3600
    # Reject a callback whose timestamp is older than this — bounds replay.
    _MAX_SKEW_SECONDS = 3600

    def _network_code(self):
        return (settings.GAM_NETWORK_CODE or "").strip()

    def _ad_unit_path(self):
        return (settings.GAM_REWARDED_AD_UNIT_PATH or "").strip()

    def is_configured(self) -> bool:
        return bool(self._network_code() and self._ad_unit_path())

    def client_config(self) -> dict:
        return {
            "provider": self.name,
            "network_code": self._network_code(),
            "ad_unit_path": self._ad_unit_path(),
        }

    @classmethod
    def _verifier_keys(cls):
        now = time.time()
        if cls._keys_cache is not None and (now - cls._keys_fetched_at) < cls._KEYS_TTL:
            return cls._keys_cache
        try:
            with urlopen(cls.VERIFIER_KEYS_URL, timeout=10) as resp:
                doc = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # network / parse
            if cls._keys_cache is not None:
                logger.warning("Reusing cached AdMob verifier keys after fetch failure: %s", exc)
                return cls._keys_cache
            raise RewardedAdVerificationError("Could not load reward verifier keys.") from exc
        keys = {}
        for entry in doc.get("keys", []):
            key_id = str(entry.get("keyId"))
            pem = entry.get("pem")
            if key_id and pem:
                keys[key_id] = pem
        cls._keys_cache = keys
        cls._keys_fetched_at = now
        return keys

    def verify_callback(self, *, method, params, body):
        try:
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.asymmetric import ec
            from cryptography.hazmat.primitives.serialization import load_pem_public_key
        except ImportError as exc:  # pragma: no cover - dependency present in prod
            raise RewardedAdVerificationError(
                "Signature verification is unavailable on this server."
            ) from exc

        signature_b64 = params.get("signature")
        key_id = params.get("key_id")
        if not signature_b64 or not key_id:
            raise RewardedAdVerificationError("Reward callback is missing its signature.")

        # Google signs the query string up to but excluding "&signature=".
        raw_qs = params.get("__raw_query__", "")
        marker = "signature="
        idx = raw_qs.find(marker)
        if idx <= 0:
            raise RewardedAdVerificationError("Reward callback query string is malformed.")
        signed_portion = raw_qs[: idx - 1]  # drop the trailing '&' or '?'

        ts = params.get("timestamp")
        try:
            ts_seconds = int(ts) / 1000.0
        except (TypeError, ValueError) as exc:
            raise RewardedAdVerificationError("Reward callback timestamp is invalid.") from exc
        if abs(time.time() - ts_seconds) > self._MAX_SKEW_SECONDS:
            raise RewardedAdVerificationError("Reward callback timestamp is outside the allowed window.")

        keys = self._verifier_keys()
        pem = keys.get(str(key_id))
        if not pem:
            raise RewardedAdVerificationError("Reward callback signed with an unknown key.")

        try:
            signature = base64.urlsafe_b64decode(signature_b64 + "=" * (-len(signature_b64) % 4))
            public_key = load_pem_public_key(pem.encode("utf-8"))
            # AdMob/Ad Manager SSV signs the raw query-string bytes with
            # ECDSA-SHA256 (not prehashed).
            public_key.verify(signature, signed_portion.encode("utf-8"), ec.ECDSA(hashes.SHA256()))
        except RewardedAdVerificationError:
            raise
        except Exception as exc:
            raise RewardedAdVerificationError("Reward callback signature did not verify.") from exc

        user_id = read_ticket(params.get("user_id", ""))
        try:
            amount = int(params.get("reward_amount") or 0)
        except ValueError:
            amount = 0
        external_id = params.get("transaction_id") or ""
        if not external_id:
            raise RewardedAdVerificationError("Reward callback has no transaction id.")
        return RewardedAdReward(user_id=user_id, amount=amount, external_id=external_id, provider=self.name)


class PartnerRewardedAdProvider(RewardedAdProvider):
    """A generic adapter for a third-party rewarded-video network.

    Configured entirely from the environment so it can point at whichever
    network is signed with, without a code change:

    * ``PARTNER_REWARDED_SCRIPT_URL`` – the SDK script the browser loads
    * ``PARTNER_REWARDED_PLACEMENT_ID`` – the placement/zone id
    * ``PARTNER_REWARDED_SHOW_FN`` – the global JS function the frontend
      calls to show the ad (e.g. ``"aiptag.cmd.player"``)
    * ``PARTNER_REWARDED_S2S_SECRET`` – shared secret for the postback HMAC

    The expected postback (GET or POST form) carries ``user_id`` (our signed
    ticket), ``amount``, ``txn_id`` and ``sig``, where
    ``sig = hex(HMAC_SHA256(secret, f"{user_id}:{amount}:{txn_id}"))``.
    Networks vary; if the one chosen signs differently this is the single
    method to adjust.
    """

    name = "partner"

    def _secret(self):
        return (settings.PARTNER_REWARDED_S2S_SECRET or "").encode("utf-8")

    def is_configured(self) -> bool:
        return bool(
            settings.PARTNER_REWARDED_SCRIPT_URL
            and settings.PARTNER_REWARDED_PLACEMENT_ID
            and settings.PARTNER_REWARDED_S2S_SECRET
        )

    def client_config(self) -> dict:
        return {
            "provider": self.name,
            "script_url": settings.PARTNER_REWARDED_SCRIPT_URL,
            "placement_id": settings.PARTNER_REWARDED_PLACEMENT_ID,
            "show_fn": settings.PARTNER_REWARDED_SHOW_FN,
        }

    def verify_callback(self, *, method, params, body):
        user_id_raw = params.get("user_id", "")
        amount_raw = params.get("amount", "")
        txn_id = params.get("txn_id", "")
        provided_sig = params.get("sig", "")
        if not (user_id_raw and txn_id and provided_sig):
            raise RewardedAdVerificationError("Reward callback is missing required fields.")

        expected = hmac.new(
            self._secret(),
            f"{user_id_raw}:{amount_raw}:{txn_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, provided_sig.lower()):
            raise RewardedAdVerificationError("Reward callback signature did not verify.")

        user_id = read_ticket(user_id_raw)
        try:
            amount = int(amount_raw or 0)
        except ValueError:
            amount = 0
        return RewardedAdReward(user_id=user_id, amount=amount, external_id=txn_id, provider=self.name)


_PROVIDERS = {
    AdManagerRewardedAdProvider.name: AdManagerRewardedAdProvider,
    PartnerRewardedAdProvider.name: PartnerRewardedAdProvider,
}


def rewarded_ads_enabled() -> bool:
    return str(
        get_config_value("REWARDED_ADS_ENABLED", getattr(settings, "REWARDED_ADS_ENABLED", "False"))
    ).lower() == "true"


def get_rewarded_ad_provider() -> RewardedAdProvider:
    """The provider an operator selected, or the null provider when the
    feature is off or the selection isn't usable."""
    if not rewarded_ads_enabled():
        return NullRewardedAdProvider()
    selected = str(
        get_config_value(
            "REWARDED_AD_PROVIDER", getattr(settings, "REWARDED_AD_PROVIDER", "admanager")
        )
    ).strip().lower()
    provider_cls = _PROVIDERS.get(selected)
    if provider_cls is None:
        logger.warning("REWARDED_AD_PROVIDER=%r is not a known provider.", selected)
        return NullRewardedAdProvider()
    provider = provider_cls()
    if not provider.is_configured():
        logger.warning("Rewarded ad provider %r is selected but not configured.", selected)
        return NullRewardedAdProvider()
    return provider
