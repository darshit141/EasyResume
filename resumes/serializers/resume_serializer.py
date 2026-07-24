from pathlib import Path

from rest_framework import serializers

from resumes.models import Resume


class ResumeUploadSerializer(serializers.ModelSerializer):
    class Meta:
        model = Resume
        fields = [
            "id",
            "file",
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

        allowed_extensions = [".pdf", ".doc", ".docx"]

        extension = Path(value.name).suffix.lower()

        if extension not in allowed_extensions:
            raise serializers.ValidationError(
                "Only PDF, DOC, and DOCX files are allowed."
            )

        max_size = 5 * 1024 * 1024

        if value.size > max_size:
            raise serializers.ValidationError(
                "File size must not exceed 5 MB."
            )

        return value

    