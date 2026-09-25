"""Turning a classified email into a tracker status change.

A change is applied automatically only when every one of these holds:
the model (not the keyword fallback) was confident, the email is tied to
the job by its reply address or clear signals, and the sender's domain was
authenticated. Otherwise the status is only suggested and the user decides.
Every applied change records what it replaced, so it can be undone.
"""

from django.db import transaction
from django.utils import timezone

from inbox.models import InboundEmail
from resumes.models import JobResumeCuration

SAVED = JobResumeCuration.APPLICATION_STATUS_SAVED
APPLIED = JobResumeCuration.APPLICATION_STATUS_APPLIED
INTERVIEWING = JobResumeCuration.APPLICATION_STATUS_INTERVIEWING
OFFER = JobResumeCuration.APPLICATION_STATUS_OFFER
REJECTED = JobResumeCuration.APPLICATION_STATUS_REJECTED

AUTO_CONFIDENCE = 0.8
_CONFIDENT_MATCHES = {"reply_address", "signals", "ai", "manual"}

# category -> (target status, statuses it may move from). A job never moves
# backwards: an acknowledgement arriving after an interview invite changes
# nothing, and nothing but an offer overrides a rejection.
_TRANSITIONS = {
    InboundEmail.CATEGORY_ACKNOWLEDGEMENT: (APPLIED, {SAVED}),
    InboundEmail.CATEGORY_INTERVIEW: (INTERVIEWING, {SAVED, APPLIED}),
    InboundEmail.CATEGORY_ASSESSMENT: (INTERVIEWING, {SAVED, APPLIED}),
    InboundEmail.CATEGORY_REJECTION: (REJECTED, {SAVED, APPLIED, INTERVIEWING}),
    InboundEmail.CATEGORY_OFFER: (OFFER, {SAVED, APPLIED, INTERVIEWING, REJECTED}),
}


def target_status(category, current):
    rule = _TRANSITIONS.get(category)
    if not rule:
        return ""
    target, allowed_from = rule
    return target if current in allowed_from else ""


def is_auto_eligible(inbound):
    return (
        inbound.classification_source == "ai"
        and (inbound.category_confidence or 0) >= AUTO_CONFIDENCE
        and inbound.match_method in _CONFIDENT_MATCHES
        and inbound.authenticated
    )


def _set_status(curation, status):
    curation.application_status = status
    fields = ["application_status", "updated_at"]
    if status != SAVED and curation.applied_at is None:
        curation.applied_at = timezone.localdate()
        fields.append("applied_at")
    curation.save(update_fields=fields)


def evaluate(inbound):
    """Set the suggestion and, when allowed, apply it. Call after matching
    and classification."""
    if inbound.curation is None:
        inbound.suggested_status = ""
        return
    inbound.suggested_status = target_status(inbound.category, inbound.curation.application_status)
    if inbound.suggested_status and is_auto_eligible(inbound):
        apply_suggestion(inbound, automatically=True)


def apply_suggestion(inbound, automatically=False):
    """Returns True if the job's status changed."""
    with transaction.atomic():
        curation = JobResumeCuration.objects.select_for_update().get(pk=inbound.curation_id)
        target = target_status(inbound.category, curation.application_status)
        if not target:
            return False
        inbound.status_before = curation.application_status
        _set_status(curation, target)
        inbound.suggested_status = target
        inbound.status_applied_at = timezone.now()
        inbound.status_applied_automatically = automatically
        inbound.status_undone_at = None
        inbound.save()
        inbound.curation = curation
    return True


def undo(inbound):
    """Put the job back where it was, if nothing has moved it since."""
    if not inbound.status_applied_at or inbound.status_undone_at or not inbound.curation_id:
        return False
    with transaction.atomic():
        curation = JobResumeCuration.objects.select_for_update().get(pk=inbound.curation_id)
        if curation.application_status != inbound.suggested_status:
            return False
        curation.application_status = inbound.status_before or SAVED
        curation.save(update_fields=["application_status", "updated_at"])
        inbound.status_undone_at = timezone.now()
        inbound.save(update_fields=["status_undone_at"])
    return True
