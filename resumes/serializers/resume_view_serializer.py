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