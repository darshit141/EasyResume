from rest_framework import serializers

from authentication.utils.password_validator import validate_password_strength


class ResetPasswordSerializer(serializers.Serializer):
    """
    Serializer for password reset.
    """

    email = serializers.EmailField()

    password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )

    confirm_password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )

    def validate_password(self, value):
        """
        Validate password strength.
        """
        return validate_password_strength(value)

    def validate(self, attrs):
        """
        Validate password confirmation.
        """
        if attrs["password"] != attrs["confirm_password"]:
            raise serializers.ValidationError(
                {
                    "confirm_password": "Passwords do not match."
                }
            )

        return attrs