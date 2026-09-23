import logging
from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


@shared_task(bind=True, max_retries=MAX_ATTEMPTS - 1)
def process_inbound_email_task(self, inbound_id):
    """Classify, match and (for Direct Apply replies) forward one email.

    Retried with backoff: the usual failures are an AI provider hiccup or
    the email provider refusing a forward, both of which pass. After the
    last attempt the email is kept, marked failed, and visible in the admin
    stats — never dropped.
    """
    from inbox.models import InboundEmail
    from inbox.services.processing import process

    inbound = InboundEmail.objects.filter(pk=inbound_id).first()
    if inbound is None or inbound.state == InboundEmail.STATE_PROCESSED:
        return
    try:
        process(inbound)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Processing inbound email %s failed", inbound_id)
        InboundEmail.objects.filter(pk=inbound_id).update(
            processing_error=f"{type(exc).__name__}: {exc}"[:300],
            processing_attempts=inbound.processing_attempts,
        )
        if self.request.retries >= self.max_retries:
            InboundEmail.objects.filter(pk=inbound_id).update(state=InboundEmail.STATE_FAILED)
            return
        raise self.retry(exc=exc, countdown=60 * 2 ** self.request.retries)


@shared_task
def inbound_email_cleanup_task():
    """Daily: delete stored emails past retention, and clear any raw message
    left behind by processing that never finished."""
    from inbox.models import InboundEmail

    now = timezone.now()
    deleted, _ = InboundEmail.objects.filter(
        received_at__lt=now - timedelta(days=settings.INBOUND_EMAIL_RETENTION_DAYS)
    ).delete()
    cleared = InboundEmail.objects.filter(
        raw_mime__isnull=False, received_at__lt=now - timedelta(days=3)
    ).update(raw_mime=None)
    logger.info("Inbound email cleanup: %d deleted, %d raw messages cleared.", deleted, cleared)
    return {"deleted": deleted, "raw_cleared": cleared}


@shared_task
def requeue_stalled_inbound_emails_task():
    """Every few minutes: queue again any email that was stored but never
    processed (the queue was down when it arrived, or a worker died)."""
    from inbox.models import InboundEmail

    stalled = InboundEmail.objects.filter(
        state=InboundEmail.STATE_PENDING,
        received_at__lt=timezone.now() - timedelta(minutes=10),
        processing_attempts__lt=MAX_ATTEMPTS,
    ).values_list("id", flat=True)[:100]
    for inbound_id in stalled:
        process_inbound_email_task.delay(inbound_id)
    return len(stalled)
