from django.db import models

from authentication.models import User


class Resume(models.Model):
    """
    Model to store uploaded resumes.
    """

    STATUS_CHOICES = [
        ("uploaded", "Uploaded"),
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="resumes",
    )

    file = models.FileField(
        upload_to="resumes/",
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

    STATUS_CHOICES = [
        ("processing", "Processing"),
        ("completed", "Completed"),
        ("failed", "Failed"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="job_resume_curations",
    )

    job_title = models.CharField(
        max_length=200,
    )

    job_description = models.TextField()

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

    created_at = models.DateTimeField(
        auto_now_add=True,
    )

    class Meta:
        ordering = ["-version"]
        unique_together = ("curation", "version")

    def __str__(self):
        return f"{self.curation.job_title} - Tailored Resume v{self.version}"
