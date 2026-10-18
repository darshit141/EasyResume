from pathlib import Path

from rest_framework import serializers

from resumes.models import Resume
from resumes.services.llm_service import RESUME_PARSE_PROVIDERS


class ResumeUploadSerializer(serializers.ModelSerializer):
    ai_provider = serializers.ChoiceField(
        choices=RESUME_PARSE_PROVIDERS,
        required=False,
        allow_null=True,
        write_only=True,
        help_text=(
            "Which provider to use to parse this resume (ollama | groq | "
            "gemini | standard). \"standard\" uses a deterministic, non-AI "
            "parser (no API key, resume text never leaves this server, "
            "lower accuracy). Defaults to the server's configured provider."
        ),
    )

    class Meta:
        model = Resume
        fields = [
            "id",
            "file",
            "ai_provider",
            "original_filename",
            "status",
            "ats_score",
            "created_at",
        ]
        read_only_fields = [
            "id",
            "original_filename",
            "status",
            "ats_score",
            "created_at",
        ]

    def validate_file(self, value):
        """
        Validate uploaded resume file.
        """

        # Legacy ".doc" is deliberately NOT accepted: python-docx cannot read
        # the old binary Word format, so accepting it here only moved the
        # failure later into extraction, where the user got a vague "couldn't
        # read this file" instead of being told what to do about it.
        allowed_extensions = [".pdf", ".docx"]

        extension = Path(value.name).suffix.lower()

        if extension == ".doc":
            raise serializers.ValidationError(
                "The older .doc format isn't supported. Open the file and use "
                "\"Save As\" to save it as .docx or PDF, then upload that."
            )

        if extension not in allowed_extensions:
            raise serializers.ValidationError(
                f"\"{extension or 'This file type'}\" isn't supported. Please upload a PDF or DOCX resume."
            )

        if value.size == 0:
            raise serializers.ValidationError(
                "That file is empty. Please choose your resume file and try again."
            )

        max_size = 5 * 1024 * 1024

        if value.size > max_size:
            actual_mb = value.size / (1024 * 1024)
            raise serializers.ValidationError(
                f"That file is {actual_mb:.1f} MB. The maximum size is 5 MB."
            )

        # In non-test environments, verify file magic bytes / signature to prevent spoofed uploads
        from django.conf import settings
        if not getattr(settings, "RUNNING_TESTS", False):
            header = value.read(4)
            value.seek(0)
            if extension == ".pdf" and not header.startswith(b"%PDF"):
                raise serializers.ValidationError(
                    "The uploaded file does not appear to be a valid PDF document."
                )
            if extension == ".docx" and not header.startswith(b"PK\x03\x04"):
                raise serializers.ValidationError(
                    "The uploaded file does not appear to be a valid DOCX document."
                )

        return value

    