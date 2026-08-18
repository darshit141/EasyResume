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
    """

    def __init__(self, ai_service=None, provider=None):
        self.ai_service = ai_service or JobCurationAIService(provider=provider)

    def create_analysis(self, user, job_title, job_description, job_url="", resume_id=None):
        try:
            with transaction.atomic():
                curation = JobResumeCuration.objects.create(
                    user=user,
                    resume_id=resume_id,
                    job_title=job_title,
                    job_description=job_description,
                    job_url=job_url,
                    curate_resume=True,
                    status="processing",
                )
        except DatabaseError as exc:
            logger.exception("Failed to create job curation record.")
            raise JobCurationDatabaseError() from exc

        logger.info(
            "Job analysis started user_id=%s curation_id=%s", user.id, curation.id
        )
        try:
            analysis = self.ai_service.analyze_job_description(job_description)
            curation.analysis_data = analysis
            curation.status = "completed"
            curation.error_code = ""
            curation.save(update_fields=["analysis_data", "status", "error_code", "updated_at"])
            logger.info(
                "Job analysis completed user_id=%s curation_id=%s",
                user.id,
                curation.id,
            )
            return curation
        except LLMServiceError as exc:
            logger.warning(
                "Job analysis failed user_id=%s curation_id=%s code=%s",
                user.id,
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
