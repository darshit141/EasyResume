"""Reading one raw email into the parts the tracker needs."""

import email
import hashlib
import re
from email import policy
from email.utils import getaddresses, parseaddr, parsedate_to_datetime

from resumes.services.job_page_extractor import render_markup

MAX_TEXT_CHARS = 50_000


class ParsedEmail:
    def __init__(self, raw):
        self.raw = raw
        self.message = email.message_from_bytes(raw, policy=policy.default)

        name, address = parseaddr(str(self.message.get("From", "")))
        self.from_name = name.strip()[:200]
        self.from_address = address.strip().lower()[:320]
        self.subject = re.sub(r"\s+", " ", str(self.message.get("Subject", ""))).strip()[:500]

        message_id = str(self.message.get("Message-ID", "")).strip()
        self.message_id = (message_id or "sha256:" + hashlib.sha256(raw).hexdigest())[:500]

        try:
            self.sent_at = parsedate_to_datetime(str(self.message.get("Date", "")))
        except (TypeError, ValueError, IndexError):
            self.sent_at = None
        if self.sent_at is not None and self.sent_at.tzinfo is None:
            self.sent_at = None

        self.recipients = {
            address.strip().lower()
            for _, address in getaddresses(
                [str(value) for header in ("To", "Cc") for value in self.message.get_all(header, [])]
            )
            if address
        }
        self.text_body = self._text_body()
        self.attachments = self._attachments()
        self.authenticated = self._authenticated()

    def _text_body(self):
        plain = self.message.get_body(preferencelist=("plain",))
        if plain is not None:
            try:
                text = plain.get_content()
            except (LookupError, UnicodeDecodeError):
                text = ""
            if text.strip():
                return _tidy(text)
        html_part = self.message.get_body(preferencelist=("html",))
        if html_part is not None:
            try:
                return _tidy(render_markup(html_part.get_content()))
            except (LookupError, UnicodeDecodeError):
                return ""
        return ""

    def _attachments(self):
        found = []
        for part in self.message.iter_attachments():
            filename = part.get_filename()
            if not filename:
                continue
            try:
                content = part.get_payload(decode=True) or b""
            except Exception:  # noqa: BLE001 - a broken attachment must not lose the email
                continue
            found.append((filename[:200], content, part.get_content_type()))
        return found

    def _authenticated(self):
        """Whether the sending domain was verified (SPF or DKIM pass) by the
        receiving mail server. Cloudflare records its checks in
        Authentication-Results; ARC headers carry them through forwarding."""
        results = " ".join(
            str(value).lower()
            for header in ("Authentication-Results", "ARC-Authentication-Results")
            for value in self.message.get_all(header, [])
        )
        if re.search(r"\b(dkim|spf)=pass\b", results):
            return True
        return any(
            str(value).strip().lower().startswith("pass")
            for value in self.message.get_all("Received-SPF", [])
        )

    @property
    def domain(self):
        return self.from_address.rsplit("@", 1)[-1] if "@" in self.from_address else ""


def _tidy(text):
    text = text.replace("\r\n", "\n")
    text = re.sub(r"[ \t ]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()[:MAX_TEXT_CHARS]


_GMAIL_CODE = re.compile(r"confirmation code:\s*(\d{6,12})", re.IGNORECASE)
_GMAIL_LINK = re.compile(r"https://mail(?:-settings)?\.google\.com/mail/[^\s<>\"']+", re.IGNORECASE)


def gmail_forwarding_confirmation(parsed):
    """(code, link) if this is Gmail asking the user to confirm us as a
    forwarding address, else None."""
    if parsed.from_address != "forwarding-noreply@google.com":
        return None
    code = _GMAIL_CODE.search(parsed.text_body or "")
    link = _GMAIL_LINK.search(parsed.text_body or "")
    if not code and not link:
        return None
    return (code.group(1) if code else "", link.group(0)[:500] if link else "")
