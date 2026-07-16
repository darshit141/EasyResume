from rest_framework import serializers


class ForgotPasswordSerializer(serializers.Serializer):
    """
    Serializer for forgot password request.
    """

    email = serializers.EmailField()