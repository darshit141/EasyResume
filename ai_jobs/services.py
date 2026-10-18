from ai_jobs.models import AIJob
from ai_jobs.tasks import run_ai_job


def cache_ai_job_status(job):
    """Caches the serialized state of an AIJob in Redis for sub-millisecond status polling.

    High concurrency (e.g. 50-500 users) causes rapid frontend polling (/api/ai-jobs/<id>/)
    every 2 seconds. Caching serialized job data in Redis prevents dozens of repetitive
    PostgreSQL SELECT queries per second per user, keeping DB CPU and connection pools free.
    """
    from django.core.cache import cache
    from ai_jobs.serializers import AIJobSerializer

    try:
        data = AIJobSerializer(job).data
        timeout = 300 if job.is_terminal else 60
        cache.set(f"ai_job_poll:{job.id}", {"user_id": job.user_id, "data": data}, timeout=timeout)
    except Exception:
        # Cache failure must never interrupt job execution or dispatch
        pass


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
    cache_ai_job_status(job)
    run_ai_job.delay(job.id)
    return job
