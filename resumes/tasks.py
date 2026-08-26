import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task
def send_outcome_nudges_task():
    """Daily: email the one-time "did you hear back?" follow-up for every
    application that is due one.

    Kept thin on purpose - the decision logic lives in the service so it can
    be exercised directly in tests without a broker.
    """
    from resumes.services.outcome_nudge_service import send_due_outcome_nudges

    result = send_due_outcome_nudges()
    logger.info("Outcome nudge task finished: %s", result)
    return result
