"""Tracking applications the browser extension filled in on other people's sites.

The tracker already exists: a `JobResumeCuration` doubles as the user's
record of one application, and the outcome nudge and outcome insights both
read from it. So an application filled in by the extension becomes a
curation too, rather than a second parallel table that the tracker, the
nudge and the insights would each have to learn about separately.

Two rules shape everything here.

**A job we fill in is recorded, but not analysed.** The row is created with
`status=saved`: we know the job and that the user engaged with it, which is
worth tracking, but running an AI analysis nobody asked for would spend a
credit on their behalf.

**"Applied" is only ever set from something we actually saw.** Filling a
form is not applying — people fill half a form and close the tab. The
status moves to `applied` when the extension reports a real submit event in
the page, and otherwise the user is asked. `submit_detected_at` records
that we observed it, separately from `applied_at`, which is the user's own
record; if our detection is ever wrong the user can fix the status without
erasing what we saw.
"""

from datetime import timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from django.utils import timezone

from resumes.models import JobResumeCuration

# Job links are rarely clean. The same posting reaches a user as a LinkedIn
# email, a Greenhouse share link and a Google result, each carrying its own
# campaign junk. Dropping these before comparing is what stops one job
# becoming five rows in the tracker.
_TRACKING_PARAMS = frozenset(
    {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
        "gh_src", "gh_jid_src", "lever-source", "lever-origin", "ashby_jid_src",
        "src", "source", "ref", "referer", "referrer", "refid", "refid_",
        "trackingid", "trk", "trkinfo", "origin", "originaltrackingid",
        "gclid", "fbclid", "msclkid", "mc_cid", "mc_eid", "igshid",
        "recommendedflavor", "position", "pagenum", "eblanding",
    }
)

# Only ask about a fill this recent. Beyond it the user has forgotten the
# tab, and "did you apply to something a month ago?" is noise, not tracking.
PENDING_WINDOW = timedelta(days=14)

MAX_URL_LENGTH = 1000


def normalise_job_url(url):
    """A comparable form of a job link, or "" if there is nothing usable.

    Query parameters are filtered rather than dropped wholesale: several
    large boards put the job's identity *in* the query (Workday's `jobId`,
    LinkedIn's `currentJobId`), so throwing the query away would collapse
    every posting on a site into one row.
    """
    if not isinstance(url, str) or not url.strip():
        return ""

    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return ""

    if parts.scheme not in ("http", "https") or not parts.netloc:
        return ""

    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    kept = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=False)
        if key.lower() not in _TRACKING_PARAMS
    ]
    path = parts.path.rstrip("/") or "/"

    # The fragment goes: it is a client-side detail, and several boards
    # route the same posting through both #/job/123 and ?jobId=123.
    return urlunsplit(("https", host, path, urlencode(sorted(kept)), ""))


class ApplicationTrackingService:
    @staticmethod
    def record_autofill(user, *, job_url, job_title, job_description="", company=""):
        """Log that the extension filled an application form for this job.

        Returns `(curation, created)`. Filling the same posting twice
        updates the existing row instead of adding another, so the tracker
        shows one job the user applied to once — which is what happened.
        """
        title = (job_title or "").strip()[:200]
        if not title:
            raise ValueError("A job title is required to track an application.")

        key = normalise_job_url(job_url)
        existing = ApplicationTrackingService._find_by_url(user, key)
        now = timezone.now()

        if existing is not None:
            existing.autofilled_at = now
            existing.save(update_fields=["autofilled_at", "updated_at"])
            return existing, False

        curation = JobResumeCuration.objects.create(
            user=user,
            job_title=title,
            # Whatever the extension could read off the page. Often empty on
            # an application form, which is a different page from the
            # posting — an empty description is honest and still leaves a
            # trackable row. It also means "Analyse this job" can offer to
            # fetch the posting later.
            job_description=(job_description or "").strip(),
            job_url=(job_url or "").strip()[:MAX_URL_LENGTH] if key else "",
            curate_resume=False,
            status=JobResumeCuration.STATUS_SAVED,
            source=JobResumeCuration.SOURCE_EXTENSION,
            application_status=JobResumeCuration.APPLICATION_STATUS_SAVED,
            application_notes=(
                f"Filled in by the IntelliResume extension at {company.strip()}."
                if (company or "").strip()
                else "Filled in by the IntelliResume extension."
            ),
            autofilled_at=now,
        )
        return curation, True

    @staticmethod
    def create_saved_job(user, *, job_title, job_description="", job_url="", resume_id=None):
        """Track a job the user chose not to have analysed.

        Unticking "curate my resume" used to store nothing and return a
        message, so the app said "job noted" about a job that did not exist,
        and there was nothing to apply to or track afterwards. The row is
        created the same way the extension's are - status `saved`, no AI
        call, no credit spent - so the tracker and Direct Apply both work.

        Returns ``(curation, created)``; saving the same link twice reuses
        the existing row rather than adding a duplicate.
        """
        title = (job_title or "").strip()[:200]
        if not title:
            raise ValueError("A job title is required to save a job.")

        key = normalise_job_url(job_url)
        existing = ApplicationTrackingService._find_by_url(user, key)
        if existing is not None:
            return existing, False

        curation = JobResumeCuration.objects.create(
            user=user,
            job_title=title,
            job_description=(job_description or "").strip(),
            job_url=(job_url or "").strip()[:MAX_URL_LENGTH] if key else "",
            resume_id=resume_id,
            curate_resume=False,
            status=JobResumeCuration.STATUS_SAVED,
            source=JobResumeCuration.SOURCE_WEB,
            application_status=JobResumeCuration.APPLICATION_STATUS_SAVED,
        )
        return curation, True

    @staticmethod
    def record_submission(curation):
        """Record that we saw this application's form actually submitted.

        Idempotent: a page can fire submit more than once (a validation
        bounce, then the real thing), and the first observation is the one
        worth keeping.
        """
        now = timezone.now()
        fields = []

        if curation.submit_detected_at is None:
            curation.submit_detected_at = now
            fields.append("submit_detected_at")

        # Only ever promotes from "saved". If the user has already moved
        # this to interviewing or rejected, a late submit event must not
        # drag it backwards.
        if curation.application_status == JobResumeCuration.APPLICATION_STATUS_SAVED:
            curation.application_status = JobResumeCuration.APPLICATION_STATUS_APPLIED
            fields.append("application_status")
            if curation.applied_at is None:
                curation.applied_at = timezone.localdate()
                fields.append("applied_at")

        if fields:
            curation.save(update_fields=fields + ["updated_at"])
        return curation

    @staticmethod
    def dismiss_prompt(curation):
        """The user said they did not apply. Stop asking; claim nothing.

        The row stays exactly as it is — still `saved`, still in the
        tracker, with the fill still on record. All that changes is that we
        do not raise it again, because a job someone looked at and did not
        apply to is still a job they looked at.
        """
        if curation.autofill_prompt_dismissed_at is None:
            curation.autofill_prompt_dismissed_at = timezone.now()
            curation.save(update_fields=["autofill_prompt_dismissed_at", "updated_at"])
        return curation

    @staticmethod
    def pending_confirmations(user, *, limit=5):
        """Fills we never saw submitted, newest first — the ones to ask about.

        Anything we detected, anything the user has already filed, and
        anything they have already said no to is excluded: being asked to
        confirm something the system already knows is the fastest way to
        make people stop answering.
        """
        cutoff = timezone.now() - PENDING_WINDOW
        return list(
            JobResumeCuration.objects.filter(
                user=user,
                autofilled_at__isnull=False,
                autofilled_at__gte=cutoff,
                submit_detected_at__isnull=True,
                autofill_prompt_dismissed_at__isnull=True,
                application_status=JobResumeCuration.APPLICATION_STATUS_SAVED,
            ).order_by("-autofilled_at")[:limit]
        )

    @staticmethod
    def _find_by_url(user, key):
        """The user's existing curation for this posting, if there is one.

        Matched in Python rather than SQL because the stored links are raw:
        normalising them in the database would need a second indexed column
        kept in step with this function, and one user's job list is small
        enough that reading the links back is cheaper than that duplication.
        """
        if not key:
            return None

        rows = (
            JobResumeCuration.objects.filter(user=user)
            .exclude(job_url="")
            .order_by("-created_at")
            .only("id", "job_url")
        )
        for row in rows:
            if normalise_job_url(row.job_url) == key:
                # .only() left the rest of the row unloaded; callers write to
                # it, so fetch it properly now that we know which one it is.
                return JobResumeCuration.objects.get(pk=row.pk)
        return None
