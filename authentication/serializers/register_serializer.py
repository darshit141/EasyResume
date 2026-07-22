from rest_framework import serializers

from authentication.models import User
from authentication.utils.password_validator import validate_password_strength


class RegisterSerializer(serializers.ModelSerializer):
    """
    Serializer for user registration.
    """

    password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )

    confirm_password = serializers.CharField(
        write_only=True,
        style={"input_type": "password"},
    )

    class Meta:
        model = User
        fields = (
            "name",
            "email",
            "phone",
            "password",
            "confirm_password",
        )

    def validate_name(self, value):
        """
        Validate user's name.
        """
        value = value.strip()

        if len(value) < 2:
            raise serializers.ValidationError(
                "Name must contain at least 2 characters."
            )

        return value

    def validate_email(self, value):
        """
        Ensure email is unique.
        """
        value = value.lower()

        if User.objects.filter(email=value).exists():
            raise serializers.ValidationError(
                "Email already exists."
            )

        return value

    def validate_phone(self, value):
        """
        Validate phone number.
        """

        if not value.isdigit():
            raise serializers.ValidationError(
                "Phone number must contain only digits."
            )

        if len(value) != 10:
            raise serializers.ValidationError(
                "Phone number must be exactly 10 digits."
            )

        if User.objects.filter(phone=value).exists():
            raise serializers.ValidationError(
                "Phone number already exists."
            )

        return value

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