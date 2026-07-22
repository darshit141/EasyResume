from rest_framework import serializers


class VerifyLoginOTPSerializer(serializers.Serializer):
    """
    Serializer for verifying login OTP.
    """

    email = serializers.EmailField()
    otp = serializers.CharField(max_length=6)