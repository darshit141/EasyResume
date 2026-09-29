from rest_framework import serializers


class VerifyLoginOTPSerializer(serializers.Serializer):
    """
    Serializer for verifying login OTP.
    """

    email = serializers.EmailField()
    # 6 digits for an emailed or authenticator code; a backup code
    # ("abcde-fghjk") is longer.
    otp = serializers.CharField(max_length=20)