from rest_framework import serializers


class VerifyOTPSerializer(serializers.Serializer):
    """
    Serializer for OTP verification.
    """

    email = serializers.EmailField()

    otp = serializers.CharField(
        max_length=6,
        min_length=6,
    )