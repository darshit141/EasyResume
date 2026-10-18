import logging

from django.db import DatabaseError, transaction

from resumes.models import JobResumeCuration
from resumes.services.cover_letter_ai_service import CoverLetterAIService
from resumes.services.job_curation_service import JobCurationDatabaseError
from resumes.services.job_gap_analysis_service import (
    JobGapAnalysisService,
    JobRequirementsUnavailableError,
)
from resumes.services.job_resume_curation_service import JobResumeCurationService
from resumes.services.llm_service import LLMServiceError

logger = logging.getLogger(__name__)


class CoverLetterService:
    """Coordinates verified candidate data, job requirements, and match
    results into a source-grounded cover letter for a curation."""

    def __init__(self, ai_service=None, gap_analysis_service=None, provider=None, user=None):
        self.user = user
        self.ai_service = ai_service or CoverLetterAIService(provider=provider, user=user)
        self.gap_analysis_service = gap_analysis_service or JobGapAnalysisService(
            provider=provider, user=user
        )

    def generate(self, user, curation_id, regenerate=False):
        curation = JobResumeCuration.objects.get(id=curation_id, user=user)

        if curation.cover_letter_data and not regenerate:
            return curation

        if curation.status != "completed" or not curation.analysis_data:
            if curation.job_description and curation.job_description.strip():
                from resumes.services.job_curation_service import JobCurationService
                JobCurationService(llm_service=self.ai_service.llm_service, user=user).run_analysis(curation)
                curation.refresh_from_db()
            else:
                raise JobRequirementsUnavailableError()

        # Reuse the same verified-profile and match inputs the tailored
        # resume is built from, so the two documents can never disagree
        # about what the candidate actually has.
        #
        # Deliberately regenerate=False even when regenerating the letter:
        # the match analysis is only used to decide *emphasis*, never to
        # supply facts, so re-running it would spend a second AI call per
        # regeneration to change nothing material. Users who genuinely need
        # fresh matching (e.g. after editing their resume) get it from the
        # gap-analysis and tailored-resume endpoints, which do re-run it.
        gap_curation = self.gap_analysis_service.get_or_create_gap_analysis(
            user, curation_id, regenerate=False
        )
        candidate_source_data = JobResumeCurationService.build_candidate_source_data(user, resume_id=curation.resume_id)
        job_requirements = JobGapAnalysisService.build_job_requirements(curation.analysis_data)

        logger.info(
            "Cover letter generation started user_id=%s curation_id=%s regenerate=%s",
            user.id, curation_id, regenerate,
        )
        try:
            cover_letter = self.ai_service.generate_cover_letter(
                candidate_source_data=candidate_source_data,
                job_title=curation.job_title,
                job_description=curation.job_description,
                job_requirements=job_requirements,
                candidate_match_summary=gap_curation.gap_analysis_data,
            )
        except LLMServiceError as exc:
            # Never clear an existing cover letter on a failed regeneration —
            # same rule the tailored resume follows.
            logger.warning(
                "Cover letter generation failed user_id=%s curation_id=%s code=%s",
                user.id, curation_id, exc.code,
            )
            self._mark_failed(curation, exc.code)
            raise

        try:
            with transaction.atomic():
                curation.cover_letter_data = cover_letter
                curation.cover_letter_error_code = ""
                curation.save(
                    update_fields=["cover_letter_data", "cover_letter_error_code", "updated_at"]
                )
        except DatabaseError as exc:
            logger.exception("Failed to save cover letter data.")
            raise JobCurationDatabaseError() from exc

        logger.info(
            "Cover letter generation completed user_id=%s curation_id=%s", user.id, curation_id
        )
        return curation

    @staticmethod
    def _mark_failed(curation, error_code):
        try:
            curation.cover_letter_error_code = error_code
            curation.save(update_fields=["cover_letter_error_code", "updated_at"])
        except DatabaseError:
            logger.exception("Failed to persist cover letter failure state.")
