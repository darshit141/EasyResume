"""Time-based one-time passwords (RFC 6238) for authenticator apps.

Implemented directly on hmac rather than through a library: the algorithm
is thirty lines, and every mainstream authenticator app speaks exactly this
profile - SHA-1, six digits, a 30-second step.
"""

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
# Accept the previous and the next step as well, so a phone clock a little
# off or a code typed just as it rolls over still works.
DRIFT_STEPS = 1


def generate_secret():
    """A new random 160-bit secret, base32 without padding (what the apps
    expect in a setup key)."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _code_at(secret, step):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % (10 ** DIGITS)).zfill(DIGITS)


def current_step(now=None):
    return int((time.time() if now is None else now) // STEP_SECONDS)


def code_for(secret, now=None):
    """The code an app would show right now (used by tests)."""
    return _code_at(secret, current_step(now))


def matching_step(secret, code, now=None):
    """The time step ``code`` is valid for, or None. The caller records the
    step so the same code can't be used twice."""
    code = (code or "").strip().replace(" ", "")
    if len(code) != DIGITS or not code.isdigit():
        return None
    step = current_step(now)
    for candidate in range(step - DRIFT_STEPS, step + DRIFT_STEPS + 1):
        if hmac.compare_digest(_code_at(secret, candidate), code):
            return candidate
    return None


def provisioning_uri(secret, account_name, issuer):
    """The otpauth:// link a QR code encodes."""
    label = quote(f"{issuer}:{account_name}")
    query = urlencode({
        "secret": secret,
        "issuer": issuer,
        "algorithm": "SHA1",
        "digits": DIGITS,
        "period": STEP_SECONDS,
    })
    return f"otpauth://totp/{label}?{query}"


def qr_svg_data_uri(uri):
    """The setup QR code as an inline SVG data URI, so the secret never
    leaves this server for a third-party QR service."""
    import segno

    return segno.make(uri, error="m").svg_data_uri(scale=6, border=2)
