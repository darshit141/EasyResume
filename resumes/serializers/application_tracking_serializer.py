from rest_framework import serializers

from resumes.models import JobResumeCuration
from resumes.services.application_tracking_service import MAX_URL_LENGTH


class AutofillTrackRequestSerializer(serializers.Serializer):
    """What the extension knows about the page it just filled in."""

    job_title = serializers.CharField(max_length=200)
    # A CharField, not a URLField: this is the address of a page the user is
    # genuinely sitting on, and rejecting a real one because it offends a
    # validator would lose the tracking record for no benefit. The service
    # decides what is usable when it normalises it.
    job_url = serializers.CharField(max_length=MAX_URL_LENGTH, required=False, allow_blank=True, default="")
    job_description = serializers.CharField(required=False, allow_blank=True, default="")
    company = serializers.CharField(max_length=200, required=False, allow_blank=True, default="")


class TrackedApplicationSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobResumeCuration
        fields = [
            "id",
            "job_title",
            "job_url",
            "status",
            "source",
            "application_status",
            "applied_at",
            "autofilled_at",
            "submit_detected_at",
            "created_at",
        ]
        read_only_fields = fields
