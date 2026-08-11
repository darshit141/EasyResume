import logging

from django.db import DatabaseError, transaction

from resumes.models import JobResumeCuration, JobResumeCurationVersion, Resume
from resumes.services.job_curation_service import JobCurationDatabaseError
from resumes.services.job_gap_analysis_service import (
    CandidateDataUnavailableError,
    JobGapAnalysisService,
    JobRequirementsUnavailableError,
)
from resumes.services.llm_service import LLMServiceError
from resumes.services.resume_curation_ai_service import ResumeCurationAIService


logger = logging.getLogger(__name__)


class JobResumeCurationService:
    """
    Coordinates verified candidate source data, structured job requirements,
    and candidate-to-job matching results into a validated, source-grounded,
    job-tailored resume for a curation session.
    """

    def __init__(self, ai_service=None, gap_analysis_service=None):
        self.ai_service = ai_service or ResumeCurationAIService()
        self.gap_analysis_service = gap_analysis_service or JobGapAnalysisService()

    def generate(self, user, curation_id, regenerate=False):
        """Returns (curation, version). ``version`` is the JobResumeCurationVersion
        backing the returned tailored_resume_data, or None if none exists yet."""
        curation = JobResumeCuration.objects.get(id=curation_id, user=user)

        if curation.tailored_resume_data and not regenerate:
            return curation, self._latest_version(curation)

        if curation.status != "completed" or not curation.analysis_data:
            raise JobRequirementsUnavailableError()

        # Re-run candidate-vs-job matching against the latest profile before
        # generating, so a stale gap analysis never drives regeneration.
        gap_curation = self.gap_analysis_service.get_or_create_gap_analysis(
            user,
            curation_id,
            regenerate=regenerate,
        )
        candidate_source_data = self.build_candidate_source_data(user)
        job_requirements = JobGapAnalysisService.build_job_requirements(
            curation.analysis_data
        )

        action = "Resume regeneration" if regenerate else "Resume generation"
        logger.info(
            "%s started user_id=%s curation_id=%s", action, user.id, curation_id
        )
        try:
            tailored = self.ai_service.generate_tailored_resume(
                candidate_source_data=candidate_source_data,
                job_title=curation.job_title,
                job_description=curation.job_description,
                job_requirements=job_requirements,
                candidate_match_summary=gap_curation.gap_analysis_data,
            )
        except LLMServiceError as exc:
            # Never touch tailored_resume_data here: the previously persisted
            # tailored resume and its version history must survive a failed
            # regeneration attempt untouched.
            logger.warning(
                "%s failed user_id=%s curation_id=%s code=%s",
                action,
                user.id,
                curation_id,
                exc.code,
            )
            self._mark_failed(curation, exc.code)
            raise

        tailored_resume_data = {
            "personal_information": candidate_source_data["personal_information"],
            **tailored,
        }

        try:
            with transaction.atomic():
                curation.tailored_resume_data = tailored_resume_data
                curation.tailored_resume_error_code = ""
                curation.save(
                    update_fields=[
                        "tailored_resume_data",
                        "tailored_resume_error_code",
                        "updated_at",
                    ]
                )
                version = JobResumeCurationVersion.objects.create(
                    curation=curation,
                    version=self._next_version_number(curation),
                    tailored_resume_data=tailored_resume_data,
                    match_percentage=gap_curation.gap_analysis_data.get(
                        "overall_match_percentage"
                    ),
                    gap_analysis_snapshot=gap_curation.gap_analysis_data,
                )
            logger.info(
                "%s completed user_id=%s curation_id=%s version=%s",
                action,
                user.id,
                curation_id,
                version.version,
            )
            return curation, version
        except DatabaseError as exc:
            logger.exception("Failed to save job-tailored resume data.")
            raise JobCurationDatabaseError() from exc

    @staticmethod
    def _latest_version(curation):
        return curation.tailored_resume_versions.order_by("-version").first()

    @classmethod
    def _next_version_number(cls, curation):
        latest = cls._latest_version(curation)
        return 1 if latest is None else latest.version + 1

    @staticmethod
    def build_candidate_source_data(user):
        resume = Resume.objects.filter(
            user=user,
            status="completed",
        ).order_by("-updated_at", "-id").first()
        if not resume or not resume.parsed_data:
            raise CandidateDataUnavailableError()

        parsed_data = resume.parsed_data
        return {
            "resume_id": resume.id,
            "personal_information": parsed_data.get("personal_information") or {
                "name": parsed_data.get("name"),
                "email": parsed_data.get("email"),
                "phone": parsed_data.get("phone"),
            },
            "summary": parsed_data.get("summary"),
            "skills": parsed_data.get("skills", []),
            "experience": parsed_data.get("experience", []),
            "projects": parsed_data.get("projects", []),
            "education": parsed_data.get("education", []),
            "certifications": parsed_data.get("certifications", []),
            "achievements": parsed_data.get("achievements", []),
            "languages": parsed_data.get("languages", []),
        }

    @staticmethod
    def _mark_failed(curation, error_code):
        try:
            curation.tailored_resume_error_code = error_code
            curation.save(update_fields=["tailored_resume_error_code", "updated_at"])
        except DatabaseError:
            logger.exception("Failed to persist job-tailored resume failure state.")
