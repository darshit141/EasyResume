from rest_framework import serializers

from resumes.models import Resume


class ResumeViewSerializer(serializers.ModelSerializer):
    class Meta:
        model = Resume
        fields = [
            "id",
            "original_filename",
            "status",
            "ats_score",
            "parsed_data",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class ResumeListItemSerializer(serializers.ModelSerializer):
    """Summary shape for the "My Resumes" list — deliberately excludes
    `parsed_data` so the list stays small no matter how many resumes a user
    has; fetch one by id for its full content."""

    class Meta:
        model = Resume
        fields = [
            "id",
            "original_filename",
            "status",
            "ats_score",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields