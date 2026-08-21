from django.conf import settings
from django.db import models


class AIJob(models.Model):
    """One row per AI-backed operation dispatched to a Celery worker.

    This is the single source of truth for "what is the current status of
    this AI operation" — the frontend polls this row (via AIJobStatusAPIView)
    instead of blocking an HTTP request on the LLM call itself. The actual
    AI logic is untouched: a job's runner (see ai_jobs.tasks.JOB_RUNNERS)
    calls the exact same service methods that used to run inline in the
    request/response cycle, so evidence-grounding, schema validation, and
    quota accounting all behave identically — only *when* they run changes.
    """

    JOB_TYPE_RESUME_PARSE = "resume_parse"
    JOB_TYPE_JOB_ANALYSIS = "job_analysis"
    JOB_TYPE_GAP_ANALYSIS = "gap_analysis"
    JOB_TYPE_MISSING_INFORMATION_REVIEW = "missing_information_review"
    JOB_TYPE_TAILORED_RESUME_GENERATION = "tailored_resume_generation"
    JOB_TYPE_COVER_LETTER_GENERATION = "cover_letter_generation"
    JOB_TYPE_CHOICES = [
        (JOB_TYPE_RESUME_PARSE, "Resume parse"),
        (JOB_TYPE_JOB_ANALYSIS, "Job analysis"),
        (JOB_TYPE_GAP_ANALYSIS, "Gap analysis"),
        (JOB_TYPE_MISSING_INFORMATION_REVIEW, "Missing information review"),
        (JOB_TYPE_TAILORED_RESUME_GENERATION, "Tailored resume generation"),
        (JOB_TYPE_COVER_LETTER_GENERATION, "Cover letter generation"),
    ]

    STATUS_QUEUED = "queued"
    STATUS_PROCESSING = "processing"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_RETRYING = "retrying"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_COMPLETED, "Completed"),
        (STATUS_FAILED, "Failed"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_RETRYING, "Retrying"),
    ]

    # Terminal states a job can never leave — used to make cancellation and
    # duplicate-dispatch checks a one-line membership test everywhere.
    TERMINAL_STATUSES = {STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED}

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ai_jobs",
    )

    job_type = models.CharField(max_length=40, choices=JOB_TYPE_CHOICES)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_QUEUED,
    )

    # Nullable, non-exclusive: which row this job operates on. A resume-parse
    # job has no curation; every other job type has no direct resume target
    # of its own (it reads the curation's linked resume instead).
    resume = models.ForeignKey(
        "resumes.Resume",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ai_jobs",
    )
    curation = models.ForeignKey(
        "resumes.JobResumeCuration",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ai_jobs",
    )

    # Extra parameters the runner needs that aren't already on the target
    # row itself (e.g. {"regenerate": true}). Deliberately small — the job
    # never carries prompt/response content, only routing/control data.
    input_params = models.JSONField(default=dict, blank=True)

    provider = models.CharField(max_length=20, blank=True)

    error_code = models.CharField(max_length=80, blank=True)
    error_message = models.CharField(max_length=500, blank=True)

    # Only populated for job types whose full response bundle isn't already
    # re-fetchable through an existing GET endpoint (tailored resume
    # generation bundles a gap review + version diff that's never persisted
    # on its own; cover letter generation has no separate GET endpoint at
    # all). Job types that ARE re-fetchable (job analysis, gap analysis,
    # missing-information review, resume parse) leave this empty and rely
    # on `curation`/`resume` instead — no reason to duplicate data that's
    # already sitting on the row the frontend can re-GET.
    output_data = models.JSONField(default=dict, blank=True)

    celery_task_id = models.CharField(max_length=255, blank=True)
    attempt_count = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.job_type} ({self.status}) - {self.user.email}"

    @property
    def is_terminal(self):
        return self.status in self.TERMINAL_STATUSES
