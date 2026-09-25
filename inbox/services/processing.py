"""Receiving one email and everything that follows from it.

receive() runs inside the inbound webhook and only does what must be
synchronous: check the recipient, parse, de-duplicate, store. process() runs
in a Celery task with retries, because it calls the AI and may send mail.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.core.mail import EmailMessage
from django.db import IntegrityError, transaction
from django.utils import timezone

from api.utils.config import get_config_value
from billing.services.entitlements_service import EntitlementsService
from inbox.models import InboundEmail, TrackingAddress
from inbox.services import classifier, matching, status_updates
from inbox.services.addresses import resolve_recipient
from inbox.services.mime import ParsedEmail, gmail_forwarding_confirmation
from resumes.models import JobResumeCuration

logger = logging.getLogger(__name__)

TRACKING_STRATEGIES = ("alias", "gmail")


class UnknownRecipient(Exception):
    pass


class DailyLimitReached(Exception):
    pass


def receive(raw, envelope_to):
    """Store one delivered email. Returns (inbound, created)."""
    resolved = resolve_recipient(envelope_to)
    if resolved is None:
        raise UnknownRecipient(envelope_to)
    route, user, application = resolved

    since = timezone.now() - timedelta(days=1)
    if InboundEmail.objects.filter(user=user, received_at__gte=since).count() >= settings.INBOUND_EMAIL_DAILY_LIMIT:
        raise DailyLimitReached(envelope_to)

    parsed = ParsedEmail(raw)
    existing = InboundEmail.objects.filter(user=user, message_id=parsed.message_id).first()
    if existing:
        return existing, False

    try:
        with transaction.atomic():
            inbound = InboundEmail.objects.create(
                user=user,
                route=route,
                message_id=parsed.message_id,
                to_address=envelope_to.strip().lower()[:320],
                from_address=parsed.from_address,
                from_name=parsed.from_name,
                subject=parsed.subject,
                text_body=parsed.text_body,
                sent_at=parsed.sent_at,
                attachment_names=[name for name, _, _ in parsed.attachments],
                authenticated=parsed.authenticated,
                raw_mime=raw,
                curation=application.curation if application is not None else None,
                match_method="reply_address" if application is not None else "",
            )
    except IntegrityError:
        return InboundEmail.objects.get(user=user, message_id=parsed.message_id), False
    return inbound, True


def tracking_allowed(user):
    return EntitlementsService.for_user(user)["reply_tracking"] in TRACKING_STRATEGIES


def job_limit(user):
    entitlements = EntitlementsService.for_user(user)
    if entitlements["plan_id"] != "free":
        return None
    try:
        return int(get_config_value("REPLY_TRACKING_FREE_JOB_LIMIT", settings.REPLY_TRACKING_FREE_JOB_LIMIT))
    except (TypeError, ValueError):
        return settings.REPLY_TRACKING_FREE_JOB_LIMIT


def _over_job_limit(user, curation):
    limit = job_limit(user)
    if limit is None:
        return False
    tracked = set(
        InboundEmail.objects.filter(user=user, curation__isnull=False).values_list("curation_id", flat=True)
    )
    return curation.id not in tracked and len(tracked) >= limit


def _forward(inbound, parsed):
    """Pass a Direct Apply reply on to the user, when it didn't reach them
    directly. Reply-To is the recruiter, so answering goes straight back to
    them rather than through us."""
    user = inbound.user
    if user.email.lower() in parsed.recipients:
        return
    sender = inbound.from_name or inbound.from_address
    body = (
        f"{sender} replied to your application"
        + (f" for {inbound.curation.job_title}" if inbound.curation else "")
        + ". Their message is below; reply to this email to answer them directly.\n\n"
        + f"From: {sender} <{inbound.from_address}>\n"
        + f"Subject: {inbound.subject}\n\n"
        + (inbound.text_body or "(no text)")
    )
    message = EmailMessage(
        subject=f"Reply from {sender}: {inbound.subject}"[:300],
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[user.email],
        reply_to=[inbound.from_address] if inbound.from_address else None,
    )
    for name, content, content_type in parsed.attachments:
        message.attach(name, content, content_type)
    message.send(fail_silently=False)
    inbound.forwarded_at = timezone.now()


def process(inbound, llm_service=None):
    """Match, classify, update the tracker and forward. Safe to re-run: a
    processed email is left alone."""
    if inbound.state == InboundEmail.STATE_PROCESSED:
        return inbound
    inbound.processing_attempts += 1
    parsed = ParsedEmail(bytes(inbound.raw_mime)) if inbound.raw_mime else None
    if parsed is None:
        inbound.state = InboundEmail.STATE_FAILED
        inbound.processing_error = "The message content was no longer available."
        inbound.save()
        return inbound

    tracking = TrackingAddress.objects.filter(user=inbound.user).first()
    if tracking and inbound.route == InboundEmail.ROUTE_TRACKING and not tracking.first_email_received_at:
        tracking.first_email_received_at = timezone.now()
        tracking.save(update_fields=["first_email_received_at"])

    confirmation = gmail_forwarding_confirmation(parsed)
    if confirmation:
        code, link = confirmation
        if tracking:
            tracking.forwarding_confirmation_code = code
            tracking.forwarding_confirmation_link = link
            tracking.forwarding_confirmation_received_at = timezone.now()
            tracking.save()
        inbound.is_forwarding_confirmation = True
        inbound.category = InboundEmail.CATEGORY_NOT_JOB
        return _finish(inbound)

    if inbound.route == InboundEmail.ROUTE_REPLY and inbound.forwarded_at is None:
        _forward(inbound, parsed)

    if not tracking_allowed(inbound.user):
        return _finish(inbound)

    shortlist = []
    if inbound.curation is None:
        curation, method, shortlist = matching.match(parsed, inbound.user)
        inbound.curation, inbound.match_method = curation, method

    result = classifier.classify(parsed, shortlist, llm_service=llm_service)
    inbound.category = result["category"]
    inbound.category_confidence = result["confidence"]
    inbound.classification_source = result["source"]
    inbound.summary = result["summary"]
    inbound.interview_at = result["interview_at"]
    inbound.meeting_link = result["meeting_link"]
    if inbound.curation is None and result["job_id"]:
        inbound.curation = JobResumeCuration.objects.get(pk=result["job_id"])
        inbound.match_method = "ai"

    if inbound.category == InboundEmail.CATEGORY_NOT_JOB and inbound.match_method != "reply_address":
        inbound.curation, inbound.match_method = None, ""

    if inbound.curation is not None and _over_job_limit(inbound.user, inbound.curation):
        inbound.locked_curation, inbound.curation = inbound.curation, None

    status_updates.evaluate(inbound)
    return _finish(inbound)


def _finish(inbound):
    inbound.state = InboundEmail.STATE_PROCESSED
    inbound.processing_error = ""
    inbound.raw_mime = None
    inbound.save()
    return inbound
