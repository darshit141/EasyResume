from ai_jobs.models import AIJob
from ai_jobs.tasks import run_ai_job


def enqueue_ai_job(user, job_type, provider="", curation=None, resume=None, input_params=None):
    """Create the AIJob row and dispatch it to a worker in one step. The
    view that calls this has already done every synchronous check (auth,
    entitlements, request validation) — this is purely "go run the AI part
    in the background now"."""
    job = AIJob.objects.create(
        user=user,
        job_type=job_type,
        provider=provider or "",
        curation=curation,
        resume=resume,
        input_params=input_params or {},
    )
    run_ai_job.delay(job.id)
    return job
