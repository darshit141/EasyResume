"""Fetches a job posting page and extracts the posting from it.

This is the one place in the codebase that makes an outbound HTTP request to
a URL a *user* supplied, which makes it the one place exposed to SSRF — a
crafted URL tricking the server into fetching something internal (cloud
metadata endpoints, private admin services, localhost). Every guard below
exists for that reason and should not be relaxed:

- the scheme must be http/https (no file://, gopher://, ftp://)
- every resolved IP must be public (blocks localhost, RFC1918, link-local
  169.254.169.254 cloud metadata, and their IPv6 equivalents)
- the connection goes to the exact address that was checked. Resolving the
  name again when connecting would let a DNS answer that changes between
  the check and the connection (DNS rebinding) reach an internal address.
- redirects are re-validated hop by hop, never followed blindly
- the response is size-capped, and the whole import is time-capped

Any public site is accepted by default, because most postings live on
companies' own career pages. JOB_URL_ALLOW_ANY_PUBLIC_SITE=False restores
the ALLOWED_JOB_URL_DOMAINS allowlist.

Extraction prefers, in order: a platform's own JSON API, an ATS embedded on
a company page, schema.org JobPosting JSON-LD, embedded app state, the
posting found inside the page by job_page_extractor, then meta tags. Every
result goes through finalize_description, so page furniture ("Apply",
"Over 200 applicants", "Show more") never reaches the description.
"""

import contextvars
import html
import ipaddress
import json
import logging
import re
import socket
import time
import zlib
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import certifi
from django.conf import settings
from urllib3 import HTTPConnectionPool, HTTPSConnectionPool, Timeout
from urllib3.exceptions import HTTPError as Urllib3Error
from urllib3.exceptions import TimeoutError as Urllib3TimeoutError

from api.utils.config import get_config_value
from resumes.services.apify_job_fetch_service import fetch_via_apify
from resumes.services.job_page_extractor import (
    JobPageNotAPostingError,
    extract_job_content,
    finalize_description,
    job_signal_count,
    render_element_with_class,
    render_markup,
)

logger = logging.getLogger(__name__)

_MAX_BYTES = 2 * 1024 * 1024
_MAX_REDIRECTS = 5
# Per-request ceiling; the overall import budget usually binds first.
_REQUEST_TIMEOUT_SECONDS = 8
_CONNECT_TIMEOUT_SECONDS = 4
_DEFAULT_TIME_BUDGET_SECONDS = 8.0
# A realistic browser UA. These are public, unauthenticated endpoints meant
# for public consumption (LinkedIn's is the one that powers embedded job
# widgets), but several reject obviously-scripted clients outright, which
# would fail the import for postings the user can read in their own browser.
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
# Tags whose text is chrome, not content — dropped when stripping short
# fields (titles, company names) out of markup.
_NON_CONTENT_TAGS = {"script", "style", "nav", "header", "footer", "noscript", "svg", "form"}

# When the whole import must finish, as a time.monotonic() value. Set once
# per fetch_job_posting call so adapters, redirects and fallbacks all share
# one budget instead of each getting a fresh timeout.
_deadline = contextvars.ContextVar("job_import_deadline", default=None)


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
        "We couldn't read the job description from that link — the site loads it "
        "with JavaScript, which we can't run. Paste the job description instead."
    )


class JobUrlBlockedError(JobUrlFetchError):
    """The site refuses automated access, as opposed to returning a page we
    couldn't parse. Worth its own code so the message can be honest about
    *why*, and so the app can offer the browser extension, which reads the
    page in the user's own browser where these sites don't block."""

    code = "JOB_URL_BLOCKED"
    message = (
        "That job site blocks automated access, so we can't read the posting from "
        "the link. Paste the job description instead."
    )


class JobUrlNotFoundError(JobUrlFetchError):
    code = "JOB_URL_NOT_FOUND"
    message = (
        "That link returned nothing — the posting may have been removed or the URL "
        "may be incomplete. Check the link, or paste the job description instead."
    )


class JobUrlNotAPostingError(JobUrlFetchError):
    """The page loaded fine but isn't one job: a company homepage, a careers
    page listing many openings, a blog post. Returning its text as a "job
    description" would send the whole site for analysis."""

    code = "JOB_URL_NOT_A_POSTING"
    message = (
        "That link doesn't look like a single job posting — it may be a company or "
        "careers page. Open the specific job and paste its link, or paste the job "
        "description text instead."
    )


class JobUrlTimeoutError(JobUrlFetchError):
    code = "JOB_URL_TIMEOUT"
    message = (
        "That job site took too long to respond. Try again in a moment, or paste "
        "the job description instead."
    )


# ---------------------------------------------------------------------------
# Time budget
# ---------------------------------------------------------------------------

def _time_budget():
    try:
        budget = float(
            get_config_value("JOB_IMPORT_TIME_BUDGET_SECONDS", settings.JOB_IMPORT_TIME_BUDGET_SECONDS)
        )
    except (TypeError, ValueError):
        budget = _DEFAULT_TIME_BUDGET_SECONDS
    return max(budget, 1.0)


def _remaining_seconds():
    deadline = _deadline.get()
    if deadline is None:
        return float(_REQUEST_TIMEOUT_SECONDS)
    remaining = deadline - time.monotonic()
    if remaining <= 0.25:
        raise JobUrlTimeoutError()
    return remaining


# ---------------------------------------------------------------------------
# Address and domain policy
# ---------------------------------------------------------------------------

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


def _is_public_ip(ip):
    ip = _effective_ip(ip)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _public_ips(hostname):
    """Every address the host resolves to, or [] if any one of them is not
    public. A hostname can resolve to several addresses, and a single
    private one is enough to be dangerous."""
    try:
        infos = socket.getaddrinfo(hostname, None)
    except (socket.gaierror, UnicodeError):
        return []

    ips = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return []
        if not _is_public_ip(ip):
            return []
        if ip not in ips:
            ips.append(ip)
    return ips


def _is_public_address(hostname):
    return bool(_public_ips(hostname))


def _host_matches(hostname, domain):
    return hostname == domain or hostname.endswith(f".{domain}")


def is_job_domain_allowed(hostname):
    """Whether links to this host may be imported at all. Says nothing about
    whether the host is safe to reach — _read_page checks that."""
    hostname = (hostname or "").lower()
    if not hostname:
        return False
    if get_config_value("JOB_URL_ALLOW_ANY_PUBLIC_SITE", settings.JOB_URL_ALLOW_ANY_PUBLIC_SITE):
        return True
    allowed = get_config_value("ALLOWED_JOB_URL_DOMAINS", settings.ALLOWED_JOB_URL_DOMAINS)
    # An explicitly empty list means the restriction was intentionally
    # disabled — any public site passes.
    if not allowed:
        return True
    return any(_host_matches(hostname, domain) for domain in allowed)


# Nicer names for the sites people actually paste, for the error message.
_SITE_NAMES = {
    "naukri": "Naukri", "indeed": "Indeed", "glassdoor": "Glassdoor",
    "ziprecruiter": "ZipRecruiter", "monster": "Monster", "wellfound": "Wellfound",
    "angel": "Wellfound", "foundit": "Foundit", "shine": "Shine",
}


def bot_walled_site(hostname):
    """The display name of the site if it's one known to refuse server-side
    requests, else None.

    These sites answer a server with a 403 or simply hang, so trying costs
    the user the whole time budget for a guaranteed failure. Entries are
    domains ("naukri.com") or a name with any suffix ("indeed.*" matches
    in.indeed.com and indeed.co.uk).
    """
    hostname = (hostname or "").lower()
    padded = f".{hostname}."
    entries = get_config_value("BOT_WALLED_JOB_DOMAINS", settings.BOT_WALLED_JOB_DOMAINS) or []
    for entry in entries:
        entry = str(entry).strip().lower()
        if not entry:
            continue
        if entry.endswith(".*"):
            base = entry[:-2]
            matched = f".{base}." in padded
        else:
            base = entry.split(".")[0]
            matched = _host_matches(hostname, entry)
        if matched:
            return _SITE_NAMES.get(base, base.capitalize())
    return None


def _validate_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise JobUrlNotAllowedError("Only http and https links are supported.")

    hostname = (parsed.hostname or "").lower()
    if not hostname or not is_job_domain_allowed(hostname):
        raise JobUrlNotAllowedError()

    if not _is_public_address(hostname):
        raise JobUrlNotAllowedError("That link doesn't point to a public address.")

    return url


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _open(parsed, ip, accept):
    """One GET to `ip`, presenting the original hostname in the Host header
    and in TLS (SNI and certificate verification), so the request is
    indistinguishable from a normal one — except that it can only go where
    the check said it could."""
    hostname = parsed.hostname.lower()
    https = parsed.scheme == "https"
    default_port = 443 if https else 80
    port = parsed.port or default_port
    remaining = _remaining_seconds()
    timeout = Timeout(
        connect=min(remaining, _CONNECT_TIMEOUT_SECONDS),
        read=min(remaining, _REQUEST_TIMEOUT_SECONDS),
    )

    common = {"port": port, "timeout": timeout, "retries": False, "maxsize": 1}
    if https:
        pool = HTTPSConnectionPool(
            str(ip),
            server_hostname=hostname,
            assert_hostname=hostname,
            cert_reqs="CERT_REQUIRED",
            ca_certs=certifi.where(),
            **common,
        )
    else:
        pool = HTTPConnectionPool(str(ip), **common)

    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    headers = {
        "Host": hostname if port == default_port else f"{hostname}:{port}",
        "User-Agent": _USER_AGENT,
        "Accept": accept,
        "Accept-Encoding": "gzip, deflate",
        "Accept-Language": "en",
    }
    try:
        response = pool.urlopen(
            "GET", path, headers=headers, redirect=False, preload_content=False,
            decode_content=False, assert_same_host=False,
        )
    except Urllib3TimeoutError as exc:
        pool.close()
        raise JobUrlTimeoutError() from exc
    except (Urllib3Error, OSError) as exc:
        pool.close()
        logger.info("Job URL fetch failed to connect: %s (%s)", parsed.geturl(), exc)
        raise JobUrlFetchError("We couldn't reach that link.") from exc
    return pool, response


def _decompress(raw, content_encoding):
    """Undo the transfer compression ourselves, capped at _MAX_BYTES of
    output: a small compressed response can expand to gigabytes, and the cap
    has to apply to what it expands to, not to what came over the wire."""
    encoding = (content_encoding or "").strip().lower()
    if encoding in ("", "identity"):
        return raw[:_MAX_BYTES]
    if encoding in ("gzip", "x-gzip"):
        attempts = (16 + zlib.MAX_WBITS,)
    elif encoding == "deflate":
        # Servers disagree on whether "deflate" means zlib-wrapped or raw.
        attempts = (zlib.MAX_WBITS, -zlib.MAX_WBITS)
    else:
        raise JobUrlUnreadableError()
    for wbits in attempts:
        try:
            return zlib.decompressobj(wbits).decompress(raw, _MAX_BYTES)
        except zlib.error:
            continue
    raise JobUrlUnreadableError()


def _read_body(response):
    chunks, total = [], 0
    try:
        for chunk in response.stream(64 * 1024, decode_content=False):
            chunks.append(chunk)
            total += len(chunk)
            if total >= _MAX_BYTES:
                break
            _remaining_seconds()
    except Urllib3TimeoutError as exc:
        raise JobUrlTimeoutError() from exc
    except Urllib3Error as exc:
        raise JobUrlUnreadableError() from exc
    return _decompress(b"".join(chunks)[:_MAX_BYTES], response.headers.get("Content-Encoding"))


def _charset(content_type):
    match = re.search(r"charset=([\w-]+)", content_type or "", re.IGNORECASE)
    if match:
        try:
            "".encode(match.group(1))
            return match.group(1)
        except LookupError:
            pass
    return "utf-8"


# Pages must be asked for as HTML only: some sites (Workday) answer a request
# that also accepts JSON with a tiny client-side redirect instead of the page.
_ACCEPT_HTML = "text/html,application/xhtml+xml"
_ACCEPT_JSON = "application/json"


def _read_page(url, accept=_ACCEPT_HTML):
    current = url
    for _ in range(_MAX_REDIRECTS + 1):
        _validate_url(current)
        parsed = urlparse(current)
        ips = _public_ips(parsed.hostname.lower())
        if not ips:
            raise JobUrlNotAllowedError("That link doesn't point to a public address.")
        # IPv4 first: some hosts publish AAAA records they don't actually serve.
        ip = sorted(ips, key=lambda address: address.version)[0]

        pool, response = _open(parsed, ip, accept)
        try:
            status_code = response.status
            if status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("Location")
                if not location:
                    raise JobUrlUnreadableError()
                current = urljoin(current, location)
                continue

            if status_code >= 400:
                logger.info("Job URL fetch rejected by host: HTTP %s %s", status_code, current)
                if status_code in (401, 403, 429):
                    raise JobUrlBlockedError()
                if status_code in (404, 410):
                    raise JobUrlNotFoundError()
                raise JobUrlUnreadableError()

            content_type = (response.headers.get("Content-Type") or "").lower()
            # JSON is expected from the platform APIs the adapters below call.
            if not any(kind in content_type for kind in ("html", "text", "json")):
                raise JobUrlUnreadableError()
            body = _read_body(response)
        finally:
            response.release_conn()
            pool.close()
        return body.decode(_charset(content_type), errors="replace")

    raise JobUrlUnreadableError()


# ---------------------------------------------------------------------------
# Result shaping
# ---------------------------------------------------------------------------

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


def _short(value, limit=200):
    """A single-line field (title, company, location) from text or markup."""
    text = _strip_html(html.unescape(str(value or ""))) if "<" in str(value or "") else str(value or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()[:limit]


def _result(title, description, source, *, company="", location="", employment_type="", seniority=""):
    description = finalize_description(description)
    if not description:
        return None
    return {
        "title": _short(title),
        "description": description,
        "company": _short(company),
        "location": _short(location, 300),
        "employment_type": _short(employment_type, 100),
        "seniority": _short(seniority, 100),
        "source": source,
    }


_EMPLOYMENT_TYPES = {
    "FULL_TIME": "Full-time", "PART_TIME": "Part-time", "CONTRACTOR": "Contract",
    "TEMPORARY": "Temporary", "INTERN": "Internship", "VOLUNTEER": "Volunteer",
    "PER_DIEM": "Per diem", "OTHER": "",
}


def _employment_type(value):
    values = value if isinstance(value, list) else [value]
    labels = []
    for item in values:
        if not item:
            continue
        key = str(item).strip().upper().replace("-", "_").replace(" ", "_")
        label = _EMPLOYMENT_TYPES.get(key, str(item).strip())
        if label and label not in labels:
            labels.append(label)
    return ", ".join(labels)


def _location(value, remote=False):
    places = value if isinstance(value, list) else [value]
    rendered = []
    for place in places:
        if isinstance(place, str):
            text = place
        elif isinstance(place, dict):
            address = place.get("address", place)
            if isinstance(address, str):
                text = address
            elif isinstance(address, dict):
                country = address.get("addressCountry")
                if isinstance(country, dict):
                    country = country.get("name")
                parts = [address.get("addressLocality"), address.get("addressRegion"), country]
                text = ", ".join(str(part).strip() for part in parts if part and str(part).strip())
            else:
                text = ""
        else:
            text = ""
        if text:
            # "Berlin, Germany" inside a locality that already says
            # "Germany, Berlin" adds nothing: keep each part once.
            seen = [part.strip() for piece in rendered for part in piece.split(",")]
            fresh = [part.strip() for part in text.split(",") if part.strip() and part.strip() not in seen]
            deduped = []
            for part in fresh:
                if part not in deduped:
                    deduped.append(part)
            if deduped:
                rendered.append(", ".join(deduped))
    if remote and "Remote" not in rendered:
        rendered.append("Remote")
    return "; ".join(rendered)


def _from_structured_posting(posting, source):
    org = posting.get("hiringOrganization")
    company = org.get("name") if isinstance(org, dict) else org
    remote = str(posting.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    return _result(
        posting.get("title"),
        render_markup(html.unescape(str(posting.get("description") or ""))),
        source,
        company=company or "",
        location=_location(posting.get("jobLocation"), remote=remote),
        employment_type=_employment_type(posting.get("employmentType")),
    )


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
# Each adapter takes the parsed URL and returns a result, or None if it
# can't handle that URL. Every request they make still goes through
# _read_page, so the SSRF guarantees hold.
# ---------------------------------------------------------------------------

def _first_match(pattern, text):
    match = re.search(pattern, text, re.DOTALL | re.IGNORECASE)
    return _short(match.group(1)) if match else ""


def _linkedin(parsed, url):
    # /jobs/view/<slug>-<id>, /jobs/view/<id>, or ?currentJobId=<id>
    match = re.search(r"/jobs/view/(?:[^/?#]*?-)?(\d{6,})", parsed.path)
    if not match:
        match = re.search(r"[?&]currentJobId=(\d{6,})", url)
    if not match:
        return None

    job_id = match.group(1)
    # The endpoint behind LinkedIn's public/embeddable job widget. Returns a
    # server-rendered HTML fragment, no login required. The fragment is the
    # whole job card — applicant counts, sign-in prompts, referral nudges —
    # so only the description block is taken from it.
    fragment = _read_page(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}")
    description = render_element_with_class(fragment, "show-more-less-html__markup")
    if len(description) < 100:
        return None

    criteria = {
        _short(label).lower(): _short(value)
        for label, value in re.findall(
            r'description__job-criteria-subheader[^>]*>(.*?)</h3>\s*<span[^>]*>(.*?)</span>',
            fragment,
            re.DOTALL | re.IGNORECASE,
        )
    }
    return _result(
        _first_match(r"<h2[^>]*top-card-layout__title[^>]*>(.*?)</h2>", fragment),
        description,
        "linkedin",
        company=_first_match(r'topcard__org-name-link[^>]*>(.*?)</a>', fragment),
        location=_first_match(r'class="topcard__flavor topcard__flavor--bullet"[^>]*>(.*?)</span>', fragment),
        employment_type=criteria.get("employment type", ""),
        # LinkedIn's placeholder when the poster didn't pick a level.
        seniority="" if criteria.get("seniority level", "").lower() == "not applicable"
        else criteria.get("seniority level", ""),
    )


def _greenhouse_job(board, job_id):
    payload = _read_page(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{job_id}", accept=_ACCEPT_JSON)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None
    # Greenhouse returns the body as HTML-escaped markup.
    return _result(
        data.get("title"),
        render_markup(html.unescape(str(data.get("content") or ""))),
        "greenhouse",
        company=data.get("company_name") or "",
        location=(data.get("location") or {}).get("name", ""),
    )


def _greenhouse(parsed, url):
    # boards.greenhouse.io/<board>/jobs/<id> and job-boards.greenhouse.io/...
    match = re.search(r"/([^/?#]+)/jobs/(\d+)", parsed.path)
    if match:
        return _greenhouse_job(match.group(1), match.group(2))
    # Embedded form: ?for=<board> with a separate gh_jid param.
    board_match = re.search(r"[?&]for=([^&]+)", url)
    id_match = re.search(r"[?&]gh_jid=(\d+)", url)
    if board_match and id_match:
        return _greenhouse_job(board_match.group(1), id_match.group(1))
    return None


def _lever(parsed, _url):
    match = re.search(r"/([^/?#]+)/([0-9a-fA-F-]{8,})", parsed.path)
    if not match:
        return None
    company, posting_id = match.group(1), match.group(2)

    payload = _read_page(f"https://api.lever.co/v0/postings/{company}/{posting_id}", accept=_ACCEPT_JSON)
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None

    parts = [render_markup(str(data.get("description") or ""))]
    for section in data.get("lists") or []:
        heading = _short(section.get("text"))
        body = render_markup(f"<ul>{section.get('content', '')}</ul>")
        parts.append(f"{heading}\n{body}" if heading else body)
    parts.append(render_markup(str(data.get("additional") or "")))
    categories = data.get("categories") or {}
    return _result(
        data.get("text"),
        "\n\n".join(part for part in parts if part),
        "lever",
        location=categories.get("location", ""),
        employment_type=categories.get("commitment", ""),
    )


def _smartrecruiters(parsed, _url):
    match = re.search(r"/([^/?#]+)/(\d+)", parsed.path)
    if not match:
        return None
    company, posting_id = match.group(1), match.group(2)

    payload = _read_page(
        f"https://api.smartrecruiters.com/v1/companies/{company}/postings/{posting_id}",
        accept=_ACCEPT_JSON,
    )
    try:
        data = json.loads(payload)
    except json.JSONDecodeError:
        return None

    sections = ((data.get("jobAd") or {}).get("sections") or {})
    parts = []
    for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
        section = sections.get(key) or {}
        body = render_markup(str(section.get("text") or ""))
        if body:
            heading = _short(section.get("title"))
            parts.append(f"{heading}\n{body}" if heading else body)
    place = data.get("location") or {}
    return _result(
        data.get("name"),
        "\n\n".join(parts),
        "smartrecruiters",
        company=(data.get("company") or {}).get("name", ""),
        location=", ".join(str(place.get(k)) for k in ("city", "region", "country") if place.get(k)),
        employment_type=(data.get("typeOfEmployment") or {}).get("label", ""),
        seniority=(data.get("experienceLevel") or {}).get("label", ""),
    )


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
        if _host_matches(hostname, domain):
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


def _from_embedded_ats(markup, url):
    """A company careers page wrapping a Greenhouse posting — the most
    common way companies host jobs on their own domain. The link carries the
    job id (?gh_jid=) and the page's embed script names the board, which is
    everything the Greenhouse API needs."""
    job_id = re.search(r"[?&]gh_jid=(\d+)", url)
    if not job_id:
        return None
    board = re.search(r"greenhouse\.io/embed/job_board(?:/js)?\?for=([A-Za-z0-9_-]+)", markup)
    if not board:
        board = re.search(
            r"(?:job-)?boards(?:-api)?\.greenhouse\.io/(?:v1/boards/)?(?!embed\b)([A-Za-z0-9_-]+)", markup
        )
    if not board:
        return None
    try:
        return _greenhouse_job(board.group(1), job_id.group(1))
    except JobUrlFetchError:
        # The page itself was readable; let the generic path try it.
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
            result = _from_structured_posting(posting, "embedded_state")
            if result:
                return result
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
    # A site-wide description ("Acme builds software for...") can easily be
    # 200 characters; only accept one that reads like a posting.
    if len(description) < 200 or job_signal_count(description) < 1:
        return None
    return _result(meta("og:title", "twitter:title"), description, "meta_tags")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def fetch_job_posting(url):
    """Returns {"title", "description", "company", "location",
    "employment_type", "seniority", "source"}; every value is a string and
    `description` is never empty.

    Raises JobUrlFetchError (or a subclass) if the page can't be reached, is
    known to block servers, runs out of time, or has no usable posting.
    """
    token = _deadline.set(time.monotonic() + _time_budget())
    try:
        return _fetch(url)
    finally:
        _deadline.reset(token)


def _fetch(url):
    # Before _validate_url: refusing a known-blocking site needs no DNS
    # lookup and makes no connection, so it can answer immediately.
    if urlparse(url).scheme in ("http", "https"):
        site = bot_walled_site(urlparse(url).hostname)
        if site:
            raise JobUrlBlockedError(
                f"{site} blocks automated access, so we can't read the posting from the "
                "link. Paste the job description instead."
            )

    _validate_url(url)

    adapted = _try_adapters(url)
    if adapted:
        return adapted

    markup = _read_page(url)

    embedded_ats = _from_embedded_ats(markup, url)
    if embedded_ats:
        logger.info("Imported job posting via an ATS embedded on the page: %s", url)
        return embedded_ats

    for document in _iter_json_ld(markup):
        posting = _find_job_posting(document)
        if not posting:
            continue
        result = _from_structured_posting(posting, "json_ld")
        if result:
            logger.info("Extracted job posting from JSON-LD: %s", url)
            return result

    embedded = _from_embedded_state(markup)
    if embedded:
        logger.info("Imported job posting from embedded app state: %s", url)
        return embedded

    try:
        extracted = extract_job_content(markup)
    except JobPageNotAPostingError as exc:
        logger.info("Job URL is not a single posting (%s): %s", exc, url)
        raise JobUrlNotAPostingError() from exc
    if extracted:
        result = _result(extracted["title"], extracted["description"], "page_content")
        if result:
            logger.info("Extracted job posting from page content: %s", url)
            return result

    # Meta tags come after the page's own content: they are usually a
    # truncated summary, so they only help when the body couldn't be read.
    meta_result = _from_meta_tags(markup)
    if meta_result:
        logger.info("Imported job posting from meta tags: %s", url)
        return meta_result

    # Almost always a JS-rendered shell or a login wall: the fetch succeeded,
    # but the real content was never in the HTML we received.
    logger.info(
        "Job URL had no readable posting in its HTML (likely JavaScript-rendered or gated): %s",
        url,
    )
    # Last resort, only when an Apify token is configured: a headless-browser
    # render. It has its own (longer) timeout rather than this import's
    # budget, because a render simply can't finish in a few seconds.
    try:
        rescued = fetch_via_apify(url)
    except JobPageNotAPostingError as exc:
        raise JobUrlNotAPostingError() from exc
    if rescued:
        result = _result(rescued["title"], rescued["description"], "apify")
        if result:
            return result
    raise JobUrlUnreadableError()
