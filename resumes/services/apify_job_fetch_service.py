"""A last-resort rescue for job postings the primary fetch can't read.

job_url_fetch_service.fetch_job_posting() already handles the vast majority
of postings — schema.org JobPosting JSON-LD covers LinkedIn, Indeed,
Greenhouse and Lever, and plain HTML text extraction covers most of the
rest. What it cannot do is run JavaScript, so a posting that renders
entirely client-side (common on some Workday, SmartRecruiters, BambooHR,
Breezy and JobVite instances — all sites this app already allowlists)
comes back as an empty shell and fails with JOB_URL_UNREADABLE.

This module exists only for that narrow case: a single call to Apify's
`apify/rag-web-browser` Actor, which renders the page with a real headless
browser and returns its text. It is deliberately NOT a replacement for the
primary fetch — it costs real (if small) platform usage per call, so
job_url_fetch_service only reaches for it after its own free, unauthenticated
fetch has already failed.

Fails closed in every case: no configured token, a network error, an
unexpected response shape, or too little extracted text all return None
rather than raising, so the caller falls back to its existing
JOB_URL_UNREADABLE message ("paste the description instead") exactly as it
did before this module existed. A user can never see a worse error because
of this rescue attempt — only, sometimes, no error at all.
"""

import logging

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_ACTOR_RUN_SYNC_URL = (
    "https://api.apify.com/v2/acts/apify~rag-web-browser/run-sync-get-dataset-items"
)
# Must exceed the Actor's own requestTimeoutSecs below, or we time out the
# HTTP call while the Actor is still mid-render and about to succeed.
_ACTOR_REQUEST_TIMEOUT_SECS = 40
_HTTP_TIMEOUT_SECONDS = 55
# Same floor job_url_fetch_service uses for its own text fallback — below
# this, what came back is a login wall or an empty shell, not a posting.
_MIN_DESCRIPTION_CHARS = 200
_MAX_TITLE_CHARS = 200


def fetch_via_apify(url):
    """Returns {"title": str, "description": str}, or None if the rescue
    isn't available or didn't produce a usable posting.

    Never raises — see module docstring for why that's the contract.
    """
    token = getattr(settings, "APIFY_API_TOKEN", "") or ""
    if not token:
        return None

    try:
        response = requests.post(
            _ACTOR_RUN_SYNC_URL,
            params={"token": token},
            json={
                "query": url,
                "outputFormats": ["text"],
                "scrapingTool": "browser-playwright",
                "requestTimeoutSecs": _ACTOR_REQUEST_TIMEOUT_SECS,
            },
            timeout=_HTTP_TIMEOUT_SECONDS,
        )
    except requests.RequestException:
        logger.warning("Apify job-URL rescue failed (network error): %s", url)
        return None

    if response.status_code >= 400:
        logger.warning(
            "Apify job-URL rescue failed: HTTP %s for %s", response.status_code, url
        )
        return None

    try:
        items = response.json()
    except ValueError:
        logger.warning("Apify job-URL rescue returned a non-JSON response: %s", url)
        return None

    if not items or not isinstance(items, list):
        return None

    item = items[0]
    if not isinstance(item, dict):
        return None

    description = (item.get("text") or "").strip()
    if len(description) < _MIN_DESCRIPTION_CHARS:
        logger.info(
            "Apify job-URL rescue returned only %d chars for %s — treating as unreadable.",
            len(description), url,
        )
        return None

    title = ((item.get("metadata") or {}).get("title") or "").strip()

    logger.info("Recovered job posting via Apify rescue: %s", url)
    return {"title": title[:_MAX_TITLE_CHARS], "description": description}
