"""Importing a job posting from a link, as the rest of the app sees it.

Wraps job_url_fetch_service.fetch_job_posting with the two things a single
fetch can't provide:

- a cache, so the same link pasted again (by this user or another, or in
  the preview and then the analysis) is answered instantly and doesn't hit
  the job site twice. Postings are public pages, so sharing a cached result
  across users leaks nothing.
- a record of every attempt, which is what makes reliability measurable per
  site (see stats_by_domain and the admin dashboard).

Recording is best-effort: a failure to write the record must never fail an
import that worked.
"""

import hashlib
import logging
import time
from datetime import timedelta
from urllib.parse import urlparse

from django.conf import settings
from django.core.cache import cache
from django.db.models import Avg, Count, Q
from django.utils import timezone

from api.utils.config import get_config_value
from resumes.models import JobImportAttempt
from resumes.services.application_tracking_service import normalise_job_url
from resumes.services.job_url_fetch_service import JobUrlFetchError, fetch_job_posting

logger = logging.getLogger(__name__)

# Bump when the shape or quality of an import changes, so results produced
# by older code aren't served from the cache after a deploy.
_CACHE_VERSION = "v2"


def _cache_key(normalised_url):
    digest = hashlib.sha256(normalised_url.encode("utf-8")).hexdigest()
    return f"job-import:{_CACHE_VERSION}:{digest}"


def _cache_seconds():
    try:
        return int(get_config_value("JOB_IMPORT_CACHE_SECONDS", settings.JOB_IMPORT_CACHE_SECONDS))
    except (TypeError, ValueError):
        return settings.JOB_IMPORT_CACHE_SECONDS


def _domain(url):
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def _record(*, user, trigger, url, outcome, source="", started, description=""):
    try:
        JobImportAttempt.objects.create(
            user=user if (user is not None and user.is_authenticated) else None,
            trigger=trigger,
            domain=_domain(url)[:255],
            url=(normalise_job_url(url) or url)[:1000],
            outcome=outcome[:40],
            source=source[:40],
            duration_ms=int((time.monotonic() - started) * 1000),
            description_chars=len(description or ""),
        )
    except Exception:  # noqa: BLE001 - never fail an import over bookkeeping
        logger.exception("Could not record a job import attempt for %s", url)


def import_job_posting(url, *, user=None, trigger=JobImportAttempt.TRIGGER_USER, use_cache=True):
    """Same contract as fetch_job_posting: returns the posting dict, or
    raises JobUrlFetchError."""
    started = time.monotonic()
    normalised = normalise_job_url(url) or url
    key = _cache_key(normalised)

    if use_cache:
        cached = cache.get(key)
        if cached:
            _record(
                user=user, trigger=trigger, url=url, outcome=JobImportAttempt.OUTCOME_SUCCESS,
                source="cache", started=started, description=cached.get("description"),
            )
            return dict(cached)

    try:
        result = fetch_job_posting(url)
    except JobUrlFetchError as exc:
        _record(user=user, trigger=trigger, url=url, outcome=exc.code, started=started)
        raise
    except Exception:
        _record(user=user, trigger=trigger, url=url, outcome="INTERNAL_ERROR", started=started)
        raise

    cache.set(key, result, _cache_seconds())
    _record(
        user=user, trigger=trigger, url=url, outcome=JobImportAttempt.OUTCOME_SUCCESS,
        source=result.get("source", ""), started=started, description=result.get("description"),
    )
    return result


def stats_by_domain(days=30, trigger=JobImportAttempt.TRIGGER_USER):
    """Per-site import reliability over the last `days` days, busiest first."""
    since = timezone.now() - timedelta(days=days)
    rows = (
        JobImportAttempt.objects.filter(created_at__gte=since, trigger=trigger)
        .values("domain")
        .annotate(
            attempts=Count("id"),
            successes=Count("id", filter=Q(outcome=JobImportAttempt.OUTCOME_SUCCESS)),
            avg_duration_ms=Avg("duration_ms", filter=~Q(source="cache")),
        )
        .order_by("-attempts", "domain")
    )

    failures = {}
    for row in (
        JobImportAttempt.objects.filter(created_at__gte=since, trigger=trigger)
        .exclude(outcome=JobImportAttempt.OUTCOME_SUCCESS)
        .values("domain", "outcome")
        .annotate(count=Count("id"))
        .order_by("domain", "-count")
    ):
        failures.setdefault(row["domain"], row["outcome"])

    result = []
    for row in rows:
        attempts = row["attempts"]
        result.append({
            "domain": row["domain"],
            "attempts": attempts,
            "successes": row["successes"],
            "success_rate": round(row["successes"] / attempts, 3) if attempts else 0.0,
            "avg_duration_ms": int(row["avg_duration_ms"] or 0),
            "top_failure": failures.get(row["domain"], ""),
        })
    return result


def delete_old_attempts():
    days = settings.JOB_IMPORT_ATTEMPT_RETENTION_DAYS
    deleted, _ = JobImportAttempt.objects.filter(
        created_at__lt=timezone.now() - timedelta(days=days)
    ).delete()
    return deleted
