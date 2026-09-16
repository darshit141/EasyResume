from rest_framework import serializers

from resumes.models import JobApplication


class JobApplicationSerializer(serializers.ModelSerializer):
    job_title = serializers.CharField(source="curation.job_title", read_only=True)
    job_url = serializers.CharField(source="curation.job_url", read_only=True)

    class Meta:
        model = JobApplication
        fields = [
            "id",
            "curation",
            "job_title",
            "job_url",
            "recipient_email",
            "recipient_name",
            "company_name",
            "subject",
            "body",
            "include_resume",
            "include_cover_letter",
            "resume_source",
            "status",
            "sent_at",
            "error_code",
            "attachment_filename",
            "created_at",
            "updated_at",
        ]
        # Everything the *system* decides is read-only; the user only
        # controls the message and who it goes to, and only while it is a
        # draft (enforced in the view, since DRF has no per-state concept).
        read_only_fields = [
            "id",
            "curation",
            "resume_source",
            "job_title",
            "job_url",
            "status",
            "sent_at",
            "error_code",
            "attachment_filename",
            "created_at",
            "updated_at",
        ]


class JobApplicationDraftRequestSerializer(serializers.Serializer):
    recipient_email = serializers.EmailField()
    recipient_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    company_name = serializers.CharField(max_length=200, required=False, allow_blank=True)
    include_resume = serializers.BooleanField(required=False, default=True)
    include_cover_letter = serializers.BooleanField(required=False, default=False)
    resume_source = serializers.ChoiceField(
        choices=["tailored", "master"],
        required=False,
        allow_null=True,
        help_text=(
            "Which resume to attach. Defaults to the tailored resume when one exists, "
            "otherwise the account's master resume - tailoring is never required to apply."
        ),
    )
    provider = serializers.CharField(required=False, allow_blank=True)


class JobApplicationUpdateSerializer(serializers.ModelSerializer):
    """The user's own edits to a draft before they send it."""

    class Meta:
        model = JobApplication
        fields = [
            "recipient_email",
            "recipient_name",
            "company_name",
            "subject",
            "body",
            "include_resume",
            "include_cover_letter",
        ]

    def validate_subject(self, value):
        if not value.strip():
            raise serializers.ValidationError("An application email needs a subject.")
        return value

    def validate_body(self, value):
        if not value.strip():
            raise serializers.ValidationError("An application email needs a message.")
        return value
