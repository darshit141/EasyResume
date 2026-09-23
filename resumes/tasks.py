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


@shared_task
def job_import_canary_task():
    """Daily: import one live posting per major job platform and email an
    alert if any fail. Also deletes old import records."""
    from resumes.services.job_import_canary import run_daily_check

    return run_daily_check()
