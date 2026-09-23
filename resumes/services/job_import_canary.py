"""The daily check that job-link import still works on the major platforms.

Job sites change their markup and APIs without notice, and the first person
to notice is otherwise a user whose import silently got worse. Once a day
this finds one *currently live* posting on each platform — through the
platform's own public listing API, so the check never breaks just because
a hard-coded posting expired — imports it exactly as a user would, and
emails OPS_ALERT_EMAIL if anything failed.

The sample companies below only need to keep hiring on their platform. If
one closes its board, the report says the posting couldn't be found (a
check problem), which is kept distinct from an import failure (a product
problem).
"""

import logging
import re

import requests
from django.conf import settings
from django.core.mail import send_mail

from resumes.models import JobImportAttempt
from resumes.services.job_import_service import delete_old_attempts, import_job_posting
from resumes.services.job_url_fetch_service import JobUrlFetchError

logger = logging.getLogger(__name__)

_TIMEOUT = 15
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    )
}
# Shorter than this and the import has almost certainly picked up a stub.
_MIN_DESCRIPTION_CHARS = 300


def _greenhouse():
    jobs = requests.get(
        "https://boards-api.greenhouse.io/v1/boards/airbnb/jobs", headers=_HEADERS, timeout=_TIMEOUT,
    ).json()["jobs"]
    return f"https://boards.greenhouse.io/airbnb/jobs/{jobs[0]['id']}"


def _company_careers_page():
    # Airbnb's own careers site wraps Greenhouse: exercises the path for
    # company pages, not just the ATS's own domain.
    jobs = requests.get(
        "https://boards-api.greenhouse.io/v1/boards/airbnb/jobs", headers=_HEADERS, timeout=_TIMEOUT,
    ).json()["jobs"]
    return jobs[0]["absolute_url"]


def _lever():
    postings = requests.get(
        "https://api.lever.co/v0/postings/palantir?limit=1", headers=_HEADERS, timeout=_TIMEOUT,
    ).json()
    return postings[0]["hostedUrl"]


def _ashby():
    board = requests.get(
        "https://api.ashbyhq.com/posting-api/job-board/notion", headers=_HEADERS, timeout=_TIMEOUT,
    ).json()
    return board["jobs"][0]["jobUrl"]


def _workday():
    host, tenant, site = "nvidia.wd5.myworkdayjobs.com", "nvidia", "NVIDIAExternalCareerSite"
    listing = requests.post(
        f"https://{host}/wday/cxs/{tenant}/{site}/jobs",
        json={"limit": 1, "offset": 0, "searchText": "", "appliedFacets": {}},
        headers=_HEADERS,
        timeout=_TIMEOUT,
    ).json()
    return f"https://{host}/en-US/{site}{listing['jobPostings'][0]['externalPath']}"


def _linkedin():
    page = requests.get(
        "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
        params={"keywords": "software engineer", "location": "India", "start": 0},
        headers=_HEADERS,
        timeout=_TIMEOUT,
    ).text
    match = re.search(r"/jobs/view/[a-z0-9-]*?-?(\d{8,})", page)
    return f"https://www.linkedin.com/jobs/view/{match.group(1)}"


CHECKS = (
    ("LinkedIn", _linkedin),
    ("Greenhouse", _greenhouse),
    ("Company careers page (Greenhouse)", _company_careers_page),
    ("Lever", _lever),
    ("Ashby", _ashby),
    ("Workday", _workday),
)


def run_checks():
    """Returns one dict per platform: name, url, ok, problem."""
    results = []
    for name, discover in CHECKS:
        try:
            url = discover()
        except Exception as exc:  # noqa: BLE001 - one platform must not stop the rest
            logger.warning("Job import check: couldn't find a live %s posting (%s)", name, exc)
            results.append({
                "name": name, "url": "", "ok": False,
                "problem": f"Couldn't find a live posting to test with ({type(exc).__name__}).",
            })
            continue

        try:
            posting = import_job_posting(url, trigger=JobImportAttempt.TRIGGER_CANARY, use_cache=False)
        except JobUrlFetchError as exc:
            results.append({"name": name, "url": url, "ok": False, "problem": f"Import failed: {exc.code}"})
            continue
        except Exception as exc:  # noqa: BLE001
            logger.exception("Job import check crashed for %s", url)
            results.append({"name": name, "url": url, "ok": False, "problem": f"Crashed: {type(exc).__name__}"})
            continue

        problems = []
        if not posting.get("title"):
            problems.append("no title")
        if len(posting.get("description", "")) < _MIN_DESCRIPTION_CHARS:
            problems.append(f"description only {len(posting.get('description', ''))} characters")
        results.append({
            "name": name, "url": url, "ok": not problems,
            "problem": ", ".join(problems) if problems else "",
        })
    return results


def run_daily_check():
    results = run_checks()
    delete_old_attempts()

    failed = [r for r in results if not r["ok"]]
    if failed:
        lines = [
            "The daily job-link import check found problems:",
            "",
            *[f"- {r['name']}: {r['problem']}\n  {r['url'] or '(no URL)'}" for r in failed],
            "",
            "Working: " + (", ".join(r["name"] for r in results if r["ok"]) or "none"),
            "",
            "Per-site success rates for real users are in the admin dashboard.",
        ]
        try:
            send_mail(
                subject=f"[IntelliResume] Job import check: {len(failed)} of {len(results)} failing",
                message="\n".join(lines),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[settings.OPS_ALERT_EMAIL],
                fail_silently=False,
            )
        except Exception:  # noqa: BLE001
            logger.exception("Could not send the job import check alert.")
    logger.info("Job import check: %d/%d platforms working.", len(results) - len(failed), len(results))
    return {"checked": len(results), "failed": [r["name"] for r in failed]}
