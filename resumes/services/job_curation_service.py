import logging

from django.db import DatabaseError, transaction

from resumes.models import JobResumeCuration
from resumes.services.job_curation_ai_service import JobCurationAIService
from resumes.services.llm_service import LLMServiceError


logger = logging.getLogger(__name__)


class JobCurationDatabaseError(Exception):
    code = "DATABASE_ERROR"
    message = "Unable to save job curation data."


class JobCurationService:
    """
    Coordinates persistence and AI analysis for job-specific resume curation.

    Split into two steps so the AI call can run on a background worker
    (see ai_jobs.tasks._run_job_analysis) instead of inside the HTTP
    request: `create_pending` does the fast, synchronous part (create the
    row); `run_analysis` does the slow part (call the LLM, validate,
    persist) and is what the worker calls.
    """

    def __init__(self, ai_service=None, provider=None, user=None):
        self.user = user
        self.ai_service = ai_service or JobCurationAIService(provider=provider, user=user)

    def create_pending(self, user, job_title, job_description, job_url="", resume_id=None):
        """Creates the curation session row with no AI call made yet.
        Safe to call from the request/response cycle."""
        try:
            with transaction.atomic():
                curation = JobResumeCuration.objects.create(
                    user=user,
                    resume_id=resume_id,
                    job_title=job_title,
                    job_description=job_description,
                    job_url=job_url,
                    curate_resume=True,
                    status="queued",
                )
        except DatabaseError as exc:
            logger.exception("Failed to create job curation record.")
            raise JobCurationDatabaseError() from exc

        logger.info(
            "Job analysis queued user_id=%s curation_id=%s", user.id, curation.id
        )
        return curation

    def run_analysis(self, curation):
        """Runs the actual AI job-description analysis for an already
        `create_pending`-created curation, and persists the result. Called
        by the background worker — never call this directly from a view."""
        curation.status = "processing"
        curation.save(update_fields=["status", "updated_at"])

        try:
            analysis = self.ai_service.analyze_job_description(curation.job_description)
            curation.analysis_data = analysis
            curation.status = "completed"
            curation.error_code = ""
            curation.save(update_fields=["analysis_data", "status", "error_code", "updated_at"])
            logger.info(
                "Job analysis completed user_id=%s curation_id=%s",
                curation.user_id,
                curation.id,
            )
            return curation
        except LLMServiceError as exc:
            logger.warning(
                "Job analysis failed user_id=%s curation_id=%s code=%s",
                curation.user_id,
                curation.id,
                exc.code,
            )
            self._mark_failed(curation, exc.code)
            raise
        except DatabaseError as exc:
            logger.exception("Failed to update job curation record.")
            raise JobCurationDatabaseError() from exc

    @staticmethod
    def _mark_failed(curation, error_code):
        try:
            curation.status = "failed"
            curation.error_code = error_code
            curation.save(update_fields=["status", "error_code", "updated_at"])
        except DatabaseError:
            logger.exception("Failed to persist job curation failure state.")
