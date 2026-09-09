from django.db import models

from authentication.models import User


class Resume(models.Model):
    """
    Model to store a user's resume — uploaded as a file, or assembled by
    the guided builder from an interview (see GuidedResumeSession), in which
    case there is no source file.
    """

    STATUS_CHOICES = [
        ("uploaded", "Uploaded"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    SOURCE_UPLOAD = "upload"
    SOURCE_GUIDED = "guided"
    SOURCE_CHOICES = [
        (SOURCE_UPLOAD, "Uploaded file"),
        (SOURCE_GUIDED, "Guided builder"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="resumes",
    )

    file = models.FileField(
        upload_to="resumes/",
        null=True,
        blank=True,
    )

    source = models.CharField(
        max_length=12,
        choices=SOURCE_CHOICES,
        default=SOURCE_UPLOAD,
    )

    original_filename = models.CharField(
        max_length=255,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="uploaded",
    )

    parsed_data = models.JSONField(
        default=dict,
        blank=True
    )

    ats_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.original_filename


class ResumeVersion(models.Model):
    """
    Stores every version of a resume.
    """

    resume = models.ForeignKey(
        Resume,
        on_delete=models.CASCADE,
        related_name="versions",
    )

    version = models.PositiveIntegerField()

    parsed_data = models.JSONField(
        default=dict,
        blank=True,
    )

    ats_score = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-version"]
        unique_together = ("resume", "version")

    def __str__(self):
        return f"{self.resume.original_filename} - Version {self.version}"


class JobResumeCuration(models.Model):
    """
    Stores a user's job-specific resume curation intake and AI job analysis.
    """

    # "saved" is not a stage of the AI pipeline — it means the row exists
    # purely as a tracked application and no analysis was ever asked for.
    # The browser extension creates these when it fills an application form
    # on a posting the user never captured: we know the job and that they
    # applied to it, which is worth tracking, but running an analysis nobody
    # requested would spend a credit without being asked.
    STATUS_SAVED = "saved"
    STATUS_CHOICES = [
        (STATUS_SAVED, "Saved"),
        ("queued", "Queued"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    SOURCE_WEB = "web"
    SOURCE_EXTENSION = "extension"
    SOURCE_CHOICES = [
        (SOURCE_WEB, "Web app"),
        (SOURCE_EXTENSION, "Browser extension"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="job_resume_curations",
    )

    resume = models.ForeignKey(
        "Resume",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="job_resume_curations",
    )

    job_title = models.CharField(
        max_length=200,
    )

    job_description = models.TextField()

    job_url = models.URLField(
        max_length=1000,
        blank=True,
        help_text=(
            "Link to the original posting. Kept as a reference the candidate can "
            "click back to, and used to import the posting when no description "
            "was pasted (see JobUrlFetchService)."
        ),
    )

    curate_resume = models.BooleanField(
        default=True,
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default="processing",
    )

    analysis_data = models.JSONField(
        default=dict,
        blank=True,
    )

    candidate_data_snapshot = models.JSONField(
        default=dict,
        blank=True,
    )

    gap_analysis_data = models.JSONField(
        default=dict,
        blank=True,
    )

    tailored_resume_data = models.JSONField(
        default=dict,
        blank=True,
    )

    error_code = models.CharField(
        max_length=80,
        blank=True,
    )

    gap_analysis_error_code = models.CharField(
        max_length=80,
        blank=True,
    )

    tailored_resume_error_code = models.CharField(
        max_length=80,
        blank=True,
    )

    cover_letter_data = models.JSONField(
        default=dict,
        blank=True,
    )

    cover_letter_error_code = models.CharField(
        max_length=80,
        blank=True,
    )

    # --- Application tracking -------------------------------------------
    # Lets a curation double as the user's record of that application, so
    # "which jobs have I actually applied to?" is answerable without a
    # separate model. Purely user-managed; no AI or automation touches it.
    APPLICATION_STATUS_SAVED = "saved"
    APPLICATION_STATUS_APPLIED = "applied"
    APPLICATION_STATUS_INTERVIEWING = "interviewing"
    APPLICATION_STATUS_OFFER = "offer"
    APPLICATION_STATUS_REJECTED = "rejected"
    APPLICATION_STATUS_CHOICES = [
        (APPLICATION_STATUS_SAVED, "Saved"),
        (APPLICATION_STATUS_APPLIED, "Applied"),
        (APPLICATION_STATUS_INTERVIEWING, "Interviewing"),
        (APPLICATION_STATUS_OFFER, "Offer"),
        (APPLICATION_STATUS_REJECTED, "Rejected"),
    ]

    application_status = models.CharField(
        max_length=20,
        choices=APPLICATION_STATUS_CHOICES,
        default=APPLICATION_STATUS_SAVED,
    )

    applied_at = models.DateField(
        null=True,
        blank=True,
        help_text="The date the user actually submitted this application.",
    )

    application_notes = models.TextField(
        blank=True,
        help_text="The user's own private notes about this application.",
    )

    source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        default=SOURCE_WEB,
        help_text="Where this row came from. Shown in the tracker so the user knows why a job they never typed in is listed.",
    )

    # The next two are observations, not the user's word for it, and are
    # kept apart from `application_status`/`applied_at` for the same reason
    # JobApplication is kept apart from both: what we saw happen and what
    # the user says happened must stay distinguishable. If our detection is
    # ever wrong, the user can correct the status without erasing the
    # evidence of what we actually observed.
    autofilled_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When the browser extension last filled an application form for this job.",
    )

    submit_detected_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the extension saw the filled form actually submitted. Set "
            "only from a real submit event in the page - never inferred from "
            "the fact that we filled it."
        ),
    )

    autofill_prompt_dismissed_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the user answered 'no, I didn't apply' to the extension's "
            "prompt. Recorded rather than acted on: it takes the job out of "
            "the ask-again list without deleting it or claiming an outcome "
            "that did not happen."
        ),
    )

    outcome_nudge_sent_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=(
            "When the 'did you hear back?' follow-up was emailed for this "
            "application. Set so the nudge is only ever sent once - the "
            "outcome data is only worth collecting if asking for it doesn't "
            "become a recurring annoyance."
        ),
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True,
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.job_title} ({self.user.email})"


class JobResumeCurationVersion(models.Model):
    """
    Stores every generated version of a job-tailored resume for a curation,
    so regenerating never overwrites or destroys prior AI-generated output.
    """

    curation = models.ForeignKey(
        JobResumeCuration,
        on_delete=models.CASCADE,
        related_name="tailored_resume_versions",
    )

    version = models.PositiveIntegerField()

    tailored_resume_data = models.JSONField(
        default=dict,
        blank=True,
    )

    match_percentage = models.DecimalField(
        max_digits=5,
        decimal_places=2,
        blank=True,
        null=True,
    )

    gap_analysis_snapshot = models.JSONField(
        default=dict,
        blank=True,
    )

    # The resolved resume-layout template config in force when this version
    # was generated (see resumes.resume_template.resolve_template). Snapshot
    # so a historical version always re-renders exactly as it looked when
    # created, even after the user changes their default template later. An
    # empty dict means "generated before templates existed" — every renderer
    # falls back to DEFAULT_TEMPLATE for that case.
    template_config = models.JSONField(
        default=dict,
        blank=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-version"]
        unique_together = ("curation", "version")

    def __str__(self):
        return f"{self.curation.job_title} - Tailored Resume v{self.version}"


class GuidedResumeSession(models.Model):
    """
    A short interview that assembles a resume for someone who does not have
    one to upload — a fresher, a career changer, a first-timer.

    The user answers questions (see resumes.guided_questions); their answers
    are the only source of truth. AI turns each section's answers into
    grounded {header, bullets} entries the user reviews and edits. On
    finalize, `draft_data` becomes a real Resume (source="guided").
    """

    STATUS_IN_PROGRESS = "in_progress"
    STATUS_FINALIZED = "finalized"
    STATUS_CHOICES = [
        (STATUS_IN_PROGRESS, "In progress"),
        (STATUS_FINALIZED, "Finalized"),
    ]

    # Branching situations that change which questions are asked.
    SITUATIONS = ["student", "recent_graduate", "career_changer", "first_job", "experienced"]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="guided_resume_sessions",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_IN_PROGRESS,
    )

    target_role = models.CharField(max_length=120, blank=True)
    situation = models.CharField(max_length=30, blank=True)

    # Every raw answer the user gave, keyed by question id. Repeatable
    # sections (experience/projects/education) store a list of answer dicts.
    answers = models.JSONField(default=dict, blank=True)

    # The assembled resume so far, in the same shape as Resume.parsed_data:
    # personal_information, summary, skills[], experience[], projects[],
    # education[], certifications[]. Entry sections hold {header, bullets}.
    draft_data = models.JSONField(default=dict, blank=True)

    resume = models.ForeignKey(
        Resume,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="guided_session",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Guided resume ({self.status}) - {self.user.email}"


class JobApplication(models.Model):
    """One outbound application email, from draft through to sent.

    Deliberately separate from the curation's own `application_status`:
    that field is the user's self-reported record ("I applied on the 3rd",
    typed into the tracker), whereas this is what the system *actually
    delivered* — to which address, with which documents attached, at which
    timestamp. Conflating the two would make it impossible to tell an
    application we sent from one the user sent themselves elsewhere, which
    matters both for support ("did it go out?") and for abuse review.

    Nothing is ever sent automatically. A row is created as a draft, the
    user reviews and edits it, and only an explicit send request moves it
    to `sent`. That review gate is a product requirement, not an
    implementation detail — see JobApplicationService.send.
    """

    STATUS_DRAFT = "draft"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_DRAFT, "Draft"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="job_applications",
    )

    # The curation supplies the job, the tailored resume and the cover
    # letter. An application without one would have nothing to attach and
    # no job to be about, so this is required.
    curation = models.ForeignKey(
        JobResumeCuration,
        on_delete=models.CASCADE,
        related_name="applications",
    )

    recipient_email = models.EmailField(
        help_text="The single address this application is addressed to.",
    )

    recipient_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Who it is addressed to, e.g. 'Hiring Team' or a recruiter's name.",
    )

    company_name = models.CharField(max_length=200, blank=True)

    subject = models.CharField(max_length=300)

    body = models.TextField()

    include_resume = models.BooleanField(
        default=True,
        help_text="Attach the curation's tailored resume as a PDF.",
    )

    include_cover_letter = models.BooleanField(
        default=False,
        help_text="Append the curation's generated cover letter to the email body.",
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default=STATUS_DRAFT,
    )

    sent_at = models.DateTimeField(null=True, blank=True)

    error_code = models.CharField(
        max_length=80,
        blank=True,
        help_text="Why the last send attempt failed, if it did.",
    )

    attachment_filename = models.CharField(
        max_length=255,
        blank=True,
        help_text="Filename of the resume PDF as the recipient received it.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            # The two queries this model actually serves: a user's own
            # review queue, and the monthly send-cap count.
            models.Index(fields=["user", "-created_at"]),
            models.Index(fields=["user", "status", "sent_at"]),
        ]

    def __str__(self):
        return f"{self.subject} -> {self.recipient_email} ({self.status})"
