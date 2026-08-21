from rest_framework import serializers


class AccountDeletionRequestSerializer(serializers.Serializer):
    """Requires the current password — deletion is irreversible, so a
    stolen/left-open session alone must not be enough to trigger it, the
    same re-authentication bar as changing a password."""

    password = serializers.CharField(write_only=True, style={"input_type": "password"})
