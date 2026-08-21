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

    # Explicit rather than relying on ModelSerializer's blank=True/null=True
    # inference, so validated_data reliably has a "phone" key (empty
    # string) even when the field is omitted from the request entirely —
    # register_service.py reads it unconditionally.
    phone = serializers.CharField(
        required=False,
        allow_blank=True,
        default="",
        max_length=30,
    )

    accepted_terms = serializers.BooleanField(
        write_only=True,
        help_text="Must be true — confirms the user agreed to the current Terms of Service and Privacy Policy.",
    )

    class Meta:
        model = User
        fields = (
            "name",
            "email",
            "phone",
            "password",
            "confirm_password",
            "accepted_terms",
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
        Validate phone number. Optional — a resume product has no real need
        to require it (GDPR data minimisation), and the previous "exactly
        10 digits" rule rejected every real international number (e.g.
        +353 87 123 4567), blocking European signups outright. Accepts an
        optional leading "+" and 7-15 digits, matching the E.164 range.
        """
        value = value.strip()
        if not value:
            return value

        digits = value[1:] if value.startswith("+") else value
        if not digits.isdigit() or not (7 <= len(digits) <= 15):
            raise serializers.ValidationError(
                "Enter a valid phone number (7-15 digits, optionally starting with +)."
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

    def validate_accepted_terms(self, value):
        if not value:
            raise serializers.ValidationError(
                "You must accept the Terms of Service and Privacy Policy to create an account."
            )
        return value

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