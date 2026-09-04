from rest_framework import serializers

from resumes.guided_questions import SECTIONS, SITUATIONS
from resumes.models import GuidedResumeSession


class GuidedSessionSerializer(serializers.ModelSerializer):
    sections = serializers.SerializerMethodField()
    situations = serializers.SerializerMethodField()

    class Meta:
        model = GuidedResumeSession
        fields = [
            "id", "status", "target_role", "situation",
            "answers", "draft_data", "resume_id", "sections", "situations",
        ]
        read_only_fields = fields

    def get_sections(self, _obj):
        return SECTIONS

    def get_situations(self, _obj):
        return SITUATIONS


class GuidedAnswersRequestSerializer(serializers.Serializer):
    answers = serializers.DictField(required=True)


class GuidedSectionRequestSerializer(serializers.Serializer):
    section = serializers.ChoiceField(
        choices=["experience", "projects", "education", "summary"]
    )


class GuidedAcceptRequestSerializer(serializers.Serializer):
    section = serializers.ChoiceField(choices=["experience", "projects", "summary"])
    entries = serializers.ListField(child=serializers.DictField(), required=False)
    summary = serializers.CharField(required=False, allow_blank=True)


class GuidedPolishRequestSerializer(serializers.Serializer):
    text = serializers.CharField()
    operation = serializers.ChoiceField(
        choices=["grammar", "stronger", "shorten", "expand", "professional"],
        default="grammar",
    )
