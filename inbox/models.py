"""Employer emails about a user's job applications.

Mail reaches us two ways, and never by reading anyone's mailbox:

- a recruiter replies to a Direct Apply email, whose Reply-To carries an
  address unique to that application (ReplyAddress);
- the user forwards recruiter mail (usually with a Gmail or Outlook filter)
  to their personal tracking address (TrackingAddress).

Either way the message arrives at intelliresume.online through Cloudflare
Email Routing and becomes an InboundEmail, which is matched to a tracked job,
classified, and may move that job's tracker status.
"""

from django.conf import settings
from django.db import models


class TrackingAddress(models.Model):
    """A user's personal address for forwarding recruiter mail.

    The token is the only thing that ties mail to an account, so it is long,
    random, and can be replaced (rotate) if it leaks.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="tracking_address",
    )
    token = models.CharField(max_length=32, unique=True)
    # Gmail sends a confirmation code to a new forwarding address. We receive
    # it, so the app can show it and the user never has to go looking.
    forwarding_confirmation_code = models.CharField(max_length=32, blank=True)
    forwarding_confirmation_link = models.URLField(max_length=500, blank=True)
    forwarding_confirmation_received_at = models.DateTimeField(null=True, blank=True)
    first_email_received_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"track-{self.token} ({self.user_id})"


class ReplyAddress(models.Model):
    """The Reply-To address given to one Direct Apply email."""

    application = models.OneToOneField(
        "resumes.JobApplication", on_delete=models.CASCADE, related_name="reply_address",
    )
    token = models.CharField(max_length=32, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"reply-{self.token} ({self.application_id})"


class InboundEmail(models.Model):
    ROUTE_REPLY = "reply"
    ROUTE_TRACKING = "tracking"
    ROUTE_CHOICES = [(ROUTE_REPLY, "Reply to a Direct Apply email"), (ROUTE_TRACKING, "Forwarded by the user")]

    STATE_PENDING = "pending"
    STATE_PROCESSED = "processed"
    STATE_FAILED = "failed"
    STATE_CHOICES = [(STATE_PENDING, "Pending"), (STATE_PROCESSED, "Processed"), (STATE_FAILED, "Failed")]

    CATEGORY_INTERVIEW = "interview"
    CATEGORY_ASSESSMENT = "assessment"
    CATEGORY_OFFER = "offer"
    CATEGORY_REJECTION = "rejection"
    CATEGORY_ACKNOWLEDGEMENT = "acknowledgement"
    CATEGORY_INFO_REQUEST = "info_request"
    CATEGORY_OTHER = "other"
    CATEGORY_NOT_JOB = "not_job_related"
    CATEGORY_CHOICES = [
        (CATEGORY_INTERVIEW, "Interview invitation"),
        (CATEGORY_ASSESSMENT, "Assessment or test"),
        (CATEGORY_OFFER, "Offer"),
        (CATEGORY_REJECTION, "Rejection"),
        (CATEGORY_ACKNOWLEDGEMENT, "Application received"),
        (CATEGORY_INFO_REQUEST, "Request for information"),
        (CATEGORY_OTHER, "Other update"),
        (CATEGORY_NOT_JOB, "Not about a job"),
    ]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="inbound_emails",
    )
    curation = models.ForeignKey(
        "resumes.JobResumeCuration", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="inbound_emails",
    )
    route = models.CharField(max_length=10, choices=ROUTE_CHOICES)
    state = models.CharField(max_length=10, choices=STATE_CHOICES, default=STATE_PENDING)
    # The delivery's Message-ID (or a hash of the message when it has none):
    # redeliveries of the same email are recognised and dropped.
    message_id = models.CharField(max_length=500)

    to_address = models.CharField(max_length=320)
    from_address = models.CharField(max_length=320, blank=True)
    from_name = models.CharField(max_length=200, blank=True)
    subject = models.CharField(max_length=500, blank=True)
    text_body = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    attachment_names = models.JSONField(default=list, blank=True)
    # True when SPF or DKIM passed. Only authenticated mail may change a
    # tracker status on its own: a forged "you've been rejected" must never
    # quietly move someone's job.
    authenticated = models.BooleanField(default=False)
    # The full message, kept only until it has been forwarded (when that is
    # needed) and then cleared. Attachments are never stored beyond this.
    raw_mime = models.BinaryField(null=True, blank=True)

    is_forwarding_confirmation = models.BooleanField(default=False)

    match_method = models.CharField(max_length=20, blank=True)
    # A job the email probably belongs to, held back because the Free plan's
    # tracked-job limit was reached.
    locked_curation = models.ForeignKey(
        "resumes.JobResumeCuration", on_delete=models.SET_NULL, null=True, blank=True, related_name="+",
    )

    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES, blank=True)
    category_confidence = models.FloatField(null=True, blank=True)
    classification_source = models.CharField(max_length=10, blank=True)  # "ai" | "keywords"
    summary = models.CharField(max_length=300, blank=True)
    interview_at = models.CharField(max_length=60, blank=True)
    meeting_link = models.URLField(max_length=500, blank=True)

    # Tracker status this email suggests; set whether or not it was applied.
    suggested_status = models.CharField(max_length=20, blank=True)
    status_before = models.CharField(max_length=20, blank=True)
    status_applied_at = models.DateTimeField(null=True, blank=True)
    status_applied_automatically = models.BooleanField(default=False)
    status_undone_at = models.DateTimeField(null=True, blank=True)

    forwarded_at = models.DateTimeField(null=True, blank=True)
    processing_error = models.CharField(max_length=300, blank=True)
    processing_attempts = models.PositiveSmallIntegerField(default=0)

    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "message_id"], name="inbound_email_unique_per_user"),
        ]
        indexes = [
            models.Index(fields=["user", "-received_at"]),
            models.Index(fields=["curation", "-received_at"]),
            models.Index(fields=["state"]),
            models.Index(fields=["received_at"]),
        ]

    def __str__(self):
        return f"{self.from_address}: {self.subject[:60]}"
