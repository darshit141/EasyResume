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