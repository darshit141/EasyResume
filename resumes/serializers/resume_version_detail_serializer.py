from rest_framework import serializers

from resumes.models import ResumeVersion


class ResumeVersionDetailSerializer(serializers.ModelSerializer):
    """
    Serializer for a specific resume version.
    """

    class Meta:
        model = ResumeVersion
        fields = [
            "id",
            "version",
            "parsed_data",
            "ats_score",
            "created_at",
        ]