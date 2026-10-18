from rest_framework import serializers

from resumes.models import JobResumeCuration
from resumes.services.llm_service import SUPPORTED_PROVIDERS


class CoverLetterDataSerializer(serializers.Serializer):
    greeting = serializers.CharField()
    opening_paragraph = serializers.CharField()
    body_paragraphs = serializers.ListField(child=serializers.CharField())
    closing_paragraph = serializers.CharField()
    signature_name = serializers.CharField(allow_blank=True)


class CoverLetterGenerateRequestSerializer(serializers.Serializer):
    regenerate = serializers.BooleanField(
        default=False,
        help_text="Discard the stored cover letter and generate a fresh one.",
    )
    ai_provider = serializers.ChoiceField(
        choices=SUPPORTED_PROVIDERS,
        required=False,
        allow_null=True,
        help_text="Which AI provider to use. Defaults to the server's configured provider.",
    )
    ai_model = serializers.CharField(
        required=False,
        allow_null=True,
        allow_blank=True,
        max_length=120,
        help_text="Optional AI model override.",
    )
    ai_fallback_provider = serializers.ChoiceField(
        choices=SUPPORTED_PROVIDERS,
        required=False,
        allow_null=True,
        help_text="Optional AI fallback provider.",
    )


class CoverLetterResponseSerializer(serializers.ModelSerializer):
    cover_letter_data = CoverLetterDataSerializer(read_only=True)

    class Meta:
        model = JobResumeCuration
        fields = [
            "id",
            "job_title",
            "status",
            "cover_letter_data",
            "cover_letter_error_code",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ApplicationStatusUpdateSerializer(serializers.ModelSerializer):
    """Partial update — every field is optional so the UI can change just the
    status, just the date, or just the notes without resending the rest."""

    class Meta:
        model = JobResumeCuration
        fields = ["application_status", "applied_at", "application_notes"]
        extra_kwargs = {
            "application_status": {"required": False},
            "applied_at": {"required": False, "allow_null": True},
            "application_notes": {"required": False, "allow_blank": True},
        }


class ApplicationStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobResumeCuration
        fields = [
            "id",
            "job_title",
            "job_url",
            "application_status",
            "applied_at",
            "application_notes",
            "updated_at",
        ]
        read_only_fields = ["id", "job_title", "job_url", "updated_at"]
