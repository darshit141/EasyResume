from rest_framework import serializers


class ResumeEditSerializer(serializers.Serializer):
    parsed_data = serializers.JSONField(
        required=True,
        help_text=(
            "The full replacement profile document (partial updates are "
            "not supported — send the complete object every time). Shape: "
            "{personal_information: {name, email, phone}, name, email, "
            "phone, summary, skills: [string], experience: [entry], "
            "projects: [entry], education: [entry], certifications: "
            "[entry], achievements: [string], languages: [string]}. An "
            "`entry` is {header: string, bullets: [string]} — the "
            "role/company/dates line plus one bullet per responsibility or "
            "achievement. A plain string is still accepted for an entry "
            "and is read as {header: <string>, bullets: []}."
        ),
    )


class ResumeEditResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    ats_score = serializers.FloatField(allow_null=True)