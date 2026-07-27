from rest_framework import serializers


class ResumeEditSerializer(serializers.Serializer):
    parsed_text = serializers.CharField(
        required=True,
        allow_blank=False,
        trim_whitespace=False,
    )