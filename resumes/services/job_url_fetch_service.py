"""Fetches a job posting page and extracts its title and description.

This is the one place in the codebase that makes an outbound HTTP request to
a URL a *user* supplied, which makes it the one place exposed to SSRF — a
crafted URL tricking the server into fetching something internal (cloud
metadata endpoints, private admin services, localhost). Every guard below
exists for that reason and should not be relaxed:

- the scheme must be http/https (no file://, gopher://, ftp://)
- the host must be on settings.ALLOWED_JOB_URL_DOMAINS
- every resolved IP must be public (blocks localhost, RFC1918, link-local
  169.254.169.254 cloud metadata, and their IPv6 equivalents)
- redirects are re-validated against all of the above, not followed blindly
- the response is size-capped and time-capped

Extraction prefers schema.org JobPosting JSON-LD, which most job boards and
ATS platforms embed. It is structured, unambiguous, and far more reliable
than scraping rendered markup. Plain-HTML text extraction is the fallback.
"""

import gzip
import html
import io
import ipaddress
import json
import logging
import re
import socket
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.conf import settings
from api.utils.config import get_config_value

logger = logging.getLogger(__name__)

_MAX_BYTES = 2 * 1024 * 1024
_TIMEOUT_SECONDS = 15
# A realistic browser UA. These are public, unauthenticated endpoints meant
# for public consumption (LinkedIn's is the one that powers embedded job
# widgets), but several reject obviously-scripted clients outright, which
# would fail the import for postings the user can read in their own browser.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
# Tags whose text is chrome, not content — dropped before the fallback
# text extraction so navigation and cookie banners don't become the
# "job description".
_NON_CONTENT_TAGS = {"script", "style", "nav", "header", "footer", "noscript", "svg", "form"}


class JobUrlFetchError(Exception):
    code = "JOB_URL_FETCH_FAILED"
    message = "We couldn't read that job posting."

    def __init__(self, message=None, code=None):
        self.message = message or self.message
        self.code = code or self.code
        super().__init__(self.message)


class JobUrlNotAllowedError(JobUrlFetchError):
    code = "JOB_URL_NOT_ALLOWED"
    message = "That link isn't from a supported job site."


class JobUrlUnreadableError(JobUrlFetchError):
    code = "JOB_URL_UNREADABLE"
    message = (
        "We couldn't extract the job description from that link — the page may load "
        "its content with JavaScript, which we can't run. Paste the job description "
        "text instead."
    )


class JobUrlBlockedError(JobUrlFetchError):
    """The site actively refused us (401/403/429), as opposed to returning a
    page we couldn't parse. Worth its own code so the message can be honest
    about *why* rather than guessing."""

    code = "JOB_URL_BLOCKED"
    message = (
        "That job site blocks automated access, so we can't read the posting from "
        "the link. Paste the job description text instead."
    )


class JobUrlNotFoundError(JobUrlFetchError):
    code = "JOB_URL_NOT_FOUND"
    message = (
        "That link returned nothing — the posting may have been removed or the URL "
        "may be incomplete. Check the link, or paste the job description text instead."
    )


# IPv6 transition mechanisms that carry an IPv4 address inside an IPv6 one.
# These must be unwrapped before classification: 64:ff9b::12ac:4e22 is simply
# public IPv4 18.172.78.34, but Python reports the raw IPv6 form as
# `is_reserved`. Networks using DNS64/NAT64 (common on mobile carriers and
# some ISPs) return these for ordinary public hosts, so classifying them
# naively rejects perfectly legitimate sites. Unwrapping is also *safer*: a
# NAT64 address embedding 10.0.0.1 is correctly seen as private.
_NAT64_PREFIX = ipaddress.IPv6Network("64:ff9b::/96")


def _effective_ip(ip):
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped:
            return ip.ipv4_mapped
        if ip in _NAT64_PREFIX:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if ip.sixtofour:
            return ip.sixtofour
    return ip


def _is_public_address(hostname):
    """True only if every address the host resolves to is public.

    Checks *all* results because a hostname can resolve to several addresses,
    and a single private one is enough to be dangerous.
    """
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        return False

    for info in infos:
        try:
            ip = _effective_ip(ipaddress.ip_address(info[4][0]))
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def _validate_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise JobUrlNotAllowedError("Only http and https links are supported.")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise JobUrlNotAllowedError()

    allowed = get_config_value("ALLOWED_JOB_URL_DOMAINS", settings.ALLOWED_JOB_URL_DOMAINS)
    if allowed:
        permitted = any(
            hostname == domain or hostname.endswith(f".{domain}") for domain in allowed
        )
        if not permitted:
            raise JobUrlNotAllowedError()

    if not _is_public_address(hostname):
        raise JobUrlNotAllowedError("That link doesn't point to a public address.")

    return url


class _ValidatingRedirectHandler(HTTPRedirectHandler):
    """Re-runs every guard on each redirect target.

    Without this, an allowed domain could redirect the fetch to localhost or
    a cloud metadata endpoint, defeating the checks done on the original URL.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _read_page(url):
    request = Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "text/html,application/xhtml+xml",
            # Some hosts return gzip regardless; handled below either way.
            "Accept-Encoding": "gzip, identity",
            "Accept-Language": "en",
        },
        method="GET",
    )
    opener = build_opener(_ValidatingRedirectHandler)

    try:
        with opener.open(request, timeout=_TIMEOUT_SECONDS) as response:
            content_type = (response.headers.get("Content-Type") or "").lower()
            # JSON is expected from the platform APIs the adapters below call.
            if not any(kind in content_type for kind in ("html", "text", "json")):
                raise JobUrlUnreadableError()
            raw = response.read(_MAX_BYTES + 1)
            if response.headers.get("Content-Encoding") == "gzip":
                try:
                    raw = gzip.GzipFile(fileobj=io.BytesIO(raw)).read()
                except OSError as exc:
                    raise JobUrlUnreadableError() from exc
    except HTTPError as exc:
        logger.info("Job URL fetch rejected by host: HTTP %s %s", exc.code, url)
        if exc.code in (401, 403, 429):
            raise JobUrlBlockedError() from exc
        if exc.code == 404:
            raise JobUrlNotFoundError() from exc
        raise JobUrlUnreadableError() from exc
    except (URLError, socket.timeout, TimeoutError, ConnectionError) as exc:
        logger.info("Job URL fetch failed to connect: %s (%s)", url, exc)
        raise JobUrlFetchError("We couldn't reach that link.") from exc

    return raw.decode("utf-8", errors="replace")


class _TextExtractor(HTMLParser):
    """Collects visible text, skipping non-content tags."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.chunks = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in _NON_CONTENT_TAGS:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in _NON_CONTENT_TAGS and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth:
            return
        text = data.strip()
        if text:
            self.chunks.append(text)

    def text(self):
        return "\n".join(self.chunks)


def _strip_html(markup):
    parser = _TextExtractor()
    try:
        parser.feed(markup)
    except Exception:  # noqa: BLE001 - malformed markup must not 500 the request
        logger.debug("HTML parsing failed; using whatever text was collected.")
    return re.sub(r"\n{3,}", "\n\n", parser.text()).strip()


def _iter_json_ld(markup):
    for match in re.finditer(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        markup,
        re.DOTALL | re.IGNORECASE,
    ):
        try:
            yield json.loads(html.unescape(match.group(1).strip()))
        except (json.JSONDecodeError, ValueError):
            continue


def _find_job_posting(node):
    """Depth-first search for a schema.org JobPosting, which may be nested in
    an @graph, a list, or a single object depending on the site."""
    if isinstance(node, list):
        for item in node:
            found = _find_job_posting(item)
            if found:
                return found
        return None
    if not isinstance(node, dict):
        return None

    node_type = node.get("@type")
    types = node_type if isinstance(node_type, list) else [node_type]
    if any(str(t).lower() == "jobposting" for t in types if t):
        return node

    for value in node.values():
        if isinstance(value, (dict, list)):
            found = _find_job_posting(value)
            if found:
                return found
    return None


# ---------------------------------------------------------------------------
# Platform adapters
#
# The reason a naive fetch fails on modern job sites is that the posting is
# rendered client-side — the HTML we receive is an empty shell. But these
# platforms' own front-ends get the content from public, unauthenticated JSON
# (or server-rendered fragment) endpoints. Calling the same endpoint gets the
# real posting without needing to execute any JavaScript.
#
# Each adapter takes the parsed URL and returns {"title", "description"}, or
# None if it can't handle that URL. Every request they make still goes
# through _validate_url, so the SSRF guarantees hold.
# ---------------------------------------------------------------------------

def _linkedin(parsed, url):
    # /jobs/view/<slug>-<id>, /jobs/view/<id>, or ?currentJobId=<id>
    match = re.search(r"/jobs/view/(?:[^/?#]*?-)?(\d{6,})", parsed.path)
    if not match:
        match = re.search(r"[?&]currentJobId=(\d{6,})", url)
    if not match:
        return None

    job_id = match.group(1)
    # The endpoint behind LinkedIn's public/embeddable job widget. Returns a
    # server-rendered HTML fragment, no login required.
    fragment = _read_page(
        _validate_url(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}")
    )
    description = _strip_html(fragment)
    if len(description) < 200:
        return None

    title_match = re.search(
        r'<h2[^>]*top-card-layout__title[^>]*>(.*?)</h2>', fragment, re.DOTALL | re.IGNORECASE
    )
    title = _strip_html(title_match.group(1)) if title_match else ""
    return {"title": title[:200], "description": description}


def _greenhouse(parsed, url):
    # boards.greenhouse.io/<board>/jobs/<id> and job-boards.greenhouse.io/...
    match = re.search(r"/([^/?#]+)/jobs/(\d+)", parsed.path)
    board = match.group(1) if match else None
    if not match:
        # Embedded form: ?for=<board> with a separate token/gh_jid param.
        board_match = re.search(r"[?&]for=([^&]+)", url)
        id_match = re.search(r"[?&]gh_jid=(\d+)", url)
        if not (board_match and id_match):
            return None
        board, job_id = board_match.group(1), id_match.group(1)
    else:
        job_id = match.group(2)

    payload = _read_page(
        _validate_url(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}")
    )
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    # Greenhouse returns the body as HTML-escaped markup.
    description = _strip_html(html.unescape(str(data.get("content") or "")))
    if not description:
        return None
    return {"title": str(data.get("title") or "")[:200], "description": description}


def _lever(parsed, _url):
    match = re.search(r"/([^/?#]+)/([0-9a-fA-F-]{8,})", parsed.path)
    if not match:
        return None
    company, posting_id = match.group(1), match.group(2)

    payload = _read_page(
        _validate_url(f"https://api.lever.co/v0/postings/{company}/{posting_id}")
    )
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None

    parts = [_strip_html(str(data.get("description") or ""))]
    for section in data.get("lists") or []:
        parts.append(_strip_html(f"{section.get('text', '')} {section.get('content', '')}"))
    parts.append(_strip_html(str(data.get("additional") or "")))
    description = "\n\n".join(part for part in parts if part).strip()
    if not description:
        return None
    return {"title": str(data.get("text") or "")[:200], "description": description}


def _smartrecruiters(parsed, _url):
    match = re.search(r"/([^/?#]+)/(\d+)", parsed.path)
    if not match:
        return None
    company, posting_id = match.group(1), match.group(2)

    payload = _read_page(
        _validate_url(
            f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}"
        )
    )
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None

    sections = ((data.get("jobAd") or {}).get("sections") or {})
    parts = [
        _strip_html(str((sections.get(key) or {}).get("text") or ""))
        for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation")
    ]
    description = "\n\n".join(part for part in parts if part).strip()
    if not description:
        return None
    return {"title": str(data.get("name") or "")[:200], "description": description}


_ADAPTERS = (
    ("linkedin.com", _linkedin),
    ("greenhouse.io", _greenhouse),
    ("lever.co", _lever),
    ("smartrecruiters.com", _smartrecruiters),
)


def _try_adapters(url):
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    for domain, adapter in _ADAPTERS:
        if hostname == domain or hostname.endswith(f".{domain}"):
            try:
                result = adapter(parsed, url)
            except JobUrlFetchError:
                raise
            except Exception:  # noqa: BLE001 - a broken adapter must fall back, not 500
                logger.exception("Job URL adapter for %s failed; falling back.", domain)
                return None
            if result:
                logger.info("Imported job posting via %s adapter: %s", domain, url)
            return result
    return None


def _from_embedded_state(markup):
    """Next.js/Nuxt apps ship their page data as JSON in the initial HTML.

    Covers a long tail of modern job sites that render client-side but still
    embed the posting in the document we already downloaded.
    """
    for pattern in (
        r'<script[^>]+id="__NEXT_DATA__"[^>]*>(.*?)</script>',
        r'<script[^>]*>\s*window\.__NUXT__\s*=\s*(\{.*?\})\s*;?\s*</script>',
    ):
        match = re.search(pattern, markup, re.DOTALL | re.IGNORECASE)
        if not match:
            continue
        try:
            data = json.loads(match.group(1))
        except (json.JSONDecodeError, ValueError):
            continue
        posting = _find_job_posting(data)
        if posting:
            description = _strip_html(str(posting.get("description") or ""))
            if description:
                return {
                    "title": str(posting.get("title") or "")[:200],
                    "description": description,
                }
    return None


def _from_meta_tags(markup):
    """Last structured resort: OpenGraph/meta description. Usually only a
    summary, so it's accepted only when it's substantial enough to be useful."""
    def meta(*names):
        for name in names:
            match = re.search(
                rf'<meta[^>]+(?:property|name)=["\']{name}["\'][^>]+content=["\'](.*?)["\']',
                markup, re.DOTALL | re.IGNORECASE,
            )
            if match:
                return html.unescape(match.group(1)).strip()
        return ""

    description = meta("og:description", "twitter:description", "description")
    if len(description) < 200:
        return None
    return {"title": meta("og:title", "twitter:title")[:200], "description": description}


def fetch_job_posting(url):
    """Returns {"title": str, "description": str}.

    Tries, in descending order of fidelity: a platform-specific API adapter,
    schema.org JSON-LD, embedded app state, meta tags, then raw page text.

    Raises JobUrlFetchError (or a subclass) if the page can't be reached or
    no usable description can be extracted. Never returns an empty
    description — a caller that gets a result can rely on it having content.
    """
    _validate_url(url)

    adapted = _try_adapters(url)
    if adapted:
        return adapted

    markup = _read_page(url)

    for document in _iter_json_ld(markup):
        posting = _find_job_posting(document)
        if not posting:
            continue
        description = _strip_html(str(posting.get("description") or ""))
        title = str(posting.get("title") or "").strip()
        if description:
            logger.info("Extracted job posting from JSON-LD: %s", url)
            return {"title": title[:200], "description": description}

    embedded = _from_embedded_state(markup)
    if embedded:
        logger.info("Imported job posting from embedded app state: %s", url)
        return embedded

    meta_result = _from_meta_tags(markup)
    if meta_result:
        logger.info("Imported job posting from meta tags: %s", url)
        return meta_result

    # No structured data — fall back to the page's visible text.
    text = _strip_html(markup)
    if len(text) < 200:
        # Almost always a JS-rendered shell or a login wall: the fetch
        # succeeded, but the real content was never in the HTML we received.
        logger.info(
            "Job URL had no JobPosting JSON-LD and only %d chars of text "
            "(page is likely JavaScript-rendered or gated): %s",
            len(text), url,
        )
        raise JobUrlUnreadableError()

    title_match = re.search(r"<title[^>]*>(.*?)</title>", markup, re.DOTALL | re.IGNORECASE)
    title = html.unescape(title_match.group(1)).strip() if title_match else ""
    logger.info("Extracted job posting from page text: %s", url)
    return {"title": title[:200], "description": text}
