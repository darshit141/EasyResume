"""The follow-up that asks "did you hear back?" a week after someone applies.

This exists because outcome data is the one thing this product can collect
that a general-purpose AI assistant cannot, and passive collection does not
work: a status dropdown buried in a history list gets updated by almost
nobody, so the field stays empty and every "interview rate" figure computed
from it is meaningless.

Asking at the right moment - once, a week after the application, with the
answer one click away and no login required - is what turns that field from
decoration into data.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core import signing
from django.core.mail import send_mail
from django.utils import timezone

from resumes.models import JobResumeCuration

logger = logging.getLogger(__name__)

# Namespaced so a token minted here can never be replayed against another
# signed-value feature that happens to share SECRET_KEY.
NUDGE_SALT = "resume-outcome-nudge"

# Long enough that someone who leaves the email unread for a few weeks can
# still answer it, short enough that a forwarded or leaked link does not stay
# usable indefinitely.
NUDGE_TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24 * 45


def make_outcome_token(curation):
    """A signed, expiring token identifying one application.

    Carries no secret and grants nothing except the ability to set an
    outcome on this one curation. Deliberately not an auth token: requiring
    a login to answer "did you hear back?" is exactly the friction that
    stops the data being collected at all.
    """
    return signing.dumps({"curation_id": curation.id}, salt=NUDGE_SALT)


def resolve_outcome_token(token):
    """Return the curation for a valid token, or None.

    Returns None rather than raising for every failure mode (tampered,
    expired, curation since deleted) so the caller can give one
    indistinguishable response and not leak which token exists.
    """
    try:
        payload = signing.loads(token, salt=NUDGE_SALT, max_age=NUDGE_TOKEN_MAX_AGE_SECONDS)
    except signing.BadSignature:
        return None

    curation_id = payload.get("curation_id")
    if not curation_id:
        return None

    return JobResumeCuration.objects.filter(id=curation_id).first()


def _outcome_link(token, status):
    return f"{settings.FRONTEND_URL}/outcome?t={token}&s={status}"


def send_outcome_nudge(curation):
    """Email one follow-up for one application. Returns True if sent."""

    user = curation.user
    token = make_outcome_token(curation)

    subject = f"Did you hear back about {curation.job_title}?"
    message = (
        f"Hi {user.name},\n\n"
        f"About a week ago you applied for {curation.job_title}. "
        "Any news? One click, and it helps us learn what actually gets people "
        "interviews:\n\n"
        f"  Got an interview:  {_outcome_link(token, JobResumeCuration.APPLICATION_STATUS_INTERVIEWING)}\n"
        f"  Got an offer:      {_outcome_link(token, JobResumeCuration.APPLICATION_STATUS_OFFER)}\n"
        f"  Rejected:          {_outcome_link(token, JobResumeCuration.APPLICATION_STATUS_REJECTED)}\n"
        f"  Still waiting:     {_outcome_link(token, JobResumeCuration.APPLICATION_STATUS_APPLIED)}\n\n"
        "A rejection is just as useful to record as an offer - we're looking "
        "at what distinguishes the two.\n\n"
        "We'll only ask about this application once."
    )

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user.email],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception("Failed to send outcome nudge for curation %s", curation.id)
        return False


def send_due_outcome_nudges(after_days=None):
    """Send the follow-up for every application that is due one.

    Due means: marked applied, applied at least `after_days` ago, still
    sitting at 'applied' (so nothing has been learned yet), and never
    nudged before.
    """
    from api.utils.config import get_config_value

    if after_days is None:
        after_days = get_config_value(
            "OUTCOME_NUDGE_AFTER_DAYS", settings.OUTCOME_NUDGE_AFTER_DAYS
        )

    cutoff = timezone.now().date() - timedelta(days=int(after_days))

    due = JobResumeCuration.objects.filter(
        application_status=JobResumeCuration.APPLICATION_STATUS_APPLIED,
        applied_at__isnull=False,
        applied_at__lte=cutoff,
        outcome_nudge_sent_at__isnull=True,
    ).select_related("user")

    sent = 0
    for curation in due:
        if not curation.user or not curation.user.email:
            continue
        if send_outcome_nudge(curation):
            # Stamped regardless of what the user does next: this records
            # that we asked, so nobody gets asked twice about the same
            # application.
            curation.outcome_nudge_sent_at = timezone.now()
            curation.save(update_fields=["outcome_nudge_sent_at"])
            sent += 1

    logger.info("Outcome nudges: %s due, %s sent.", due.count(), sent)
    return {"due": due.count(), "sent": sent}
