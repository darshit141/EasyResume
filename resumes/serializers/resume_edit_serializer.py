from rest_framework import serializers


class ResumeEditSerializer(serializers.Serializer):
    parsed_data = serializers.JSONField(
        required=True,
        help_text=(
            "The full replacement profile document (partial updates are "
            "not supported — send the complete object every time). Shape: "
            "{personal_information: {name, email, phone}, name, email, "
            "phone, summary, skills: [string], experience: [string], "
            "education: [string], projects: [string], "
            "certifications: [string], achievements: [string], "
            "languages: [string]}. Every list field is a flat array of "
            "free-text strings (e.g. one string per job in `experience`), "
            "not nested objects."
        ),
    )


class ResumeEditResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    ats_score = serializers.FloatField(allow_null=True)