from rest_framework import serializers


class ResumeEditSerializer(serializers.Serializer):
    parsed_data = serializers.JSONField(
        required=True,
    )