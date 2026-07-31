from rest_framework import serializers

from resumes.models import ResumeVersion


class ResumeVersionSerializer(serializers.ModelSerializer):
    """
    Serializer for resume version history.
    """

    class Meta:
        model = ResumeVersion
        fields = [
            "id",
            "version",
            "ats_score",
            "created_at",
        ]