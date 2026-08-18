from rest_framework import serializers


class LogoutSerializer(serializers.Serializer):
    """
    Serializer for logging out (blacklisting a refresh token).
    """

    refresh = serializers.CharField()
