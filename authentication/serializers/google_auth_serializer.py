from rest_framework import serializers


class GoogleSignInSerializer(serializers.Serializer):
    """The ID token ("credential") returned by Google Identity Services in
    the browser, or by the extension's auth flow. Never an access token —
    only an ID token carries the verifiable identity claims this login
    depends on."""

    credential = serializers.CharField(trim_whitespace=True)
