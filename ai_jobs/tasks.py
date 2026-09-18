"""The one Celery entry point for every AI-backed operation.

Deliberately thin: each runner below calls the exact same application
service methods that used to run inline inside the HTTP request/response
cycle (evidence-grounding, schema validation, and quota accounting are
untouched). Only the *invocation* moved off the web worker and onto a
background worker — see COMMERCIAL_READINESS_AUDIT.md P0-1.
"""

import logging
import time

from celery import shared_task
from django.db import DatabaseError
from django.utils import timezone

from ai_jobs.models import AIJob
from resumes.services.llm_service import LLMServiceError

logger = logging.getLogger(__name__)


def _run_job_analysis(job):
    from resumes.models import JobResumeCuration
    from resumes.services.job_curation_service import JobCurationService

    curation = JobResumeCuration.objects.get(id=job.curation_id)
    JobCurationService(provider=job.provider or None, user=job.user).run_analysis(curation)


def _run_gap_analysis(job):
    from resumes.services.job_gap_analysis_service import JobGapAnalysisService

    regenerate = bool(job.input_params.get("regenerate", False))
    JobGapAnalysisService(provider=job.provider or None, user=job.user).get_or_create_gap_analysis(
        job.user, job.curation_id, regenerate=regenerate
    )


def _run_missing_information_review(job):
    from resumes.services.missing_information_review_service import (
        MissingInformationReviewService,
    )

    regenerate = bool(job.input_params.get("regenerate", False))
    # The review dict itself is derived at read time from gap_analysis_data
    # (never stored on its own) — build_review() persists gap_analysis_data
    # as a side effect, so the frontend re-fetches by calling the same GET
    # endpoint again, which recomputes the review from the now-cached data
    # with no further AI call. No output_data snapshot needed.
    MissingInformationReviewService(provider=job.provider or None, user=job.user).build_review(
        job.user, job.curation_id, regenerate=regenerate
    )


def _run_tailored_resume_generation(job):
    from resumes.serializers.resume_regeneration_serializer import (
        JobResumeCurationRegenerationResponseSerializer,
    )
    from resumes.services.job_resume_curation_service import JobResumeCurationService
    from resumes.services.missing_information_review_service import (
        MissingInformationReviewService,
    )
    from resumes.services.tailored_resume_version_comparison_service import (
        TailoredResumeVersionComparisonService,
    )
    from resumes.services.resume_template_service import ResumeTemplateService

    regenerate = bool(job.input_params.get("regenerate", False))
    curation, version = JobResumeCurationService(provider=job.provider or None, user=job.user).generate(
        user=job.user, curation_id=job.curation_id, regenerate=regenerate
    )
    # Reuses the gap-analysis result `generate()` just cached — regenerate
    # is always False here, matching the original synchronous view, which
    # never re-ran matching a second time for this step.
    gap_review = MissingInformationReviewService(provider=job.provider or None, user=job.user).build_review(
        user=job.user, curation_id=job.curation_id, regenerate=False
    )
    version_comparison = TailoredResumeVersionComparisonService.compare_with_previous(curation, version)

    response_data = {
        "id": curation.id,
        "job_title": curation.job_title,
        "status": curation.status,
        "tailored_resume_version": version,
        "tailored_resume_data": curation.tailored_resume_data,
        "tailored_resume_error_code": curation.tailored_resume_error_code,
        "template_config": ResumeTemplateService.config_for_version(
            version, user=job.user
        ),
        "gap_review": gap_review,
        "version_comparison": version_comparison,
        "created_at": curation.created_at,
        "updated_at": curation.updated_at,
    }
    job.output_data = JobResumeCurationRegenerationResponseSerializer(response_data).data


def _run_cover_letter_generation(job):
    from resumes.serializers.cover_letter_serializer import CoverLetterResponseSerializer
    from resumes.services.cover_letter_service import CoverLetterService

    regenerate = bool(job.input_params.get("regenerate", False))
    curation = CoverLetterService(provider=job.provider or None, user=job.user).generate(
        user=job.user, curation_id=job.curation_id, regenerate=regenerate
    )
    job.output_data = CoverLetterResponseSerializer(curation).data


def _run_resume_parse(job):
    from resumes.models import Resume
    from resumes.services.resume_upload_service import ResumeUploadService

    resume = Resume.objects.get(id=job.resume_id)
    ResumeUploadService.run_parse(resume, provider=job.provider or None, user=job.user)


JOB_RUNNERS = {
    AIJob.JOB_TYPE_JOB_ANALYSIS: _run_job_analysis,
    AIJob.JOB_TYPE_GAP_ANALYSIS: _run_gap_analysis,
    AIJob.JOB_TYPE_MISSING_INFORMATION_REVIEW: _run_missing_information_review,
    AIJob.JOB_TYPE_TAILORED_RESUME_GENERATION: _run_tailored_resume_generation,
    AIJob.JOB_TYPE_COVER_LETTER_GENERATION: _run_cover_letter_generation,
    AIJob.JOB_TYPE_RESUME_PARSE: _run_resume_parse,
}


@shared_task(bind=True)
def run_ai_job(self, job_id):
    from resumes.services.job_curation_service import JobCurationDatabaseError
    from resumes.services.resume_upload_service import ResumeTextExtractionError

    try:
        job = AIJob.objects.select_related("user").get(id=job_id)
    except AIJob.DoesNotExist:
        logger.error("AI job %s not found when the worker picked it up.", job_id)
        return

    if job.is_terminal:
        # A duplicate task delivery (broker redelivery, manual retry) must
        # never re-run already-finished work.
        return

    runner = JOB_RUNNERS.get(job.job_type)
    if runner is None:
        _mark_failed(job, "UNKNOWN_JOB_TYPE", f"No runner registered for job_type={job.job_type}.")
        return

    job.status = AIJob.STATUS_PROCESSING
    job.started_at = timezone.now()
    job.celery_task_id = self.request.id or ""
    job.attempt_count += 1
    job.save(update_fields=["status", "started_at", "celery_task_id", "attempt_count", "updated_at"])

    start = time.monotonic()
    try:
        runner(job)
    except LLMServiceError as exc:
        logger.warning("AI job %s (%s) failed: %s", job_id, job.job_type, exc.code)
        _mark_failed(job, exc.code, str(exc))
        return
    except ResumeTextExtractionError as exc:
        logger.warning("AI job %s (%s) failed: %s", job_id, job.job_type, exc.code)
        _mark_failed(job, exc.code, str(exc))
        return
    except JobCurationDatabaseError as exc:
        logger.exception("AI job %s (%s) failed with a database error.", job_id, job.job_type)
        _mark_failed(job, exc.code, exc.message)
        return
    except DatabaseError as exc:
        logger.exception("AI job %s (%s) failed with a database error.", job_id, job.job_type)
        _mark_failed(job, "DATABASE_ERROR", str(exc))
        return
    except Exception:
        # Last-resort safety net: without this, a bug in a runner leaves the
        # job stuck in "processing" forever with no user-visible failure.
        logger.exception("AI job %s (%s) failed with an unexpected error.", job_id, job.job_type)
        _mark_failed(job, "INTERNAL_ERROR", "An unexpected error occurred.")
        return

    latency_ms = int((time.monotonic() - start) * 1000)
    job.status = AIJob.STATUS_COMPLETED
    job.completed_at = timezone.now()
    job.save(update_fields=["status", "completed_at", "output_data", "updated_at"])
    _record_usage(job, latency_ms)


def _mark_failed(job, error_code, error_message):
    job.status = AIJob.STATUS_FAILED
    job.error_code = error_code
    job.error_message = (error_message or "")[:500]
    job.completed_at = timezone.now()
    job.save(update_fields=["status", "error_code", "error_message", "completed_at", "updated_at"])


_ACTION_BY_JOB_TYPE = None


def _record_usage(job, latency_ms):
    from billing.models import AIUsageLog
    from billing.services.entitlements_service import EntitlementsService

    global _ACTION_BY_JOB_TYPE
    if _ACTION_BY_JOB_TYPE is None:
        _ACTION_BY_JOB_TYPE = {
            AIJob.JOB_TYPE_RESUME_PARSE: AIUsageLog.ACTION_RESUME_PARSE,
            AIJob.JOB_TYPE_JOB_ANALYSIS: AIUsageLog.ACTION_JOB_ANALYSIS,
            AIJob.JOB_TYPE_GAP_ANALYSIS: AIUsageLog.ACTION_GAP_ANALYSIS,
            AIJob.JOB_TYPE_MISSING_INFORMATION_REVIEW: AIUsageLog.ACTION_MISSING_INFORMATION_REVIEW,
            AIJob.JOB_TYPE_TAILORED_RESUME_GENERATION: AIUsageLog.ACTION_TAILORED_RESUME_GENERATION,
            AIJob.JOB_TYPE_COVER_LETTER_GENERATION: AIUsageLog.ACTION_COVER_LETTER,
        }
    action = _ACTION_BY_JOB_TYPE.get(job.job_type)
    if action is None:
        return
    EntitlementsService.record_ai_usage(
        job.user, action, job.provider, job=job, latency_ms=latency_ms
    )
