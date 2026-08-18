from rest_framework import serializers

from authentication.models import User


class UserProfileSerializer(serializers.ModelSerializer):
    """
    Read-only representation of the authenticated user's profile.
    """

    class Meta:
        model = User
        fields = ("id", "name", "email", "phone")
        read_only_fields = fields


class UpdateProfileSerializer(serializers.ModelSerializer):
    """
    Serializer for updating the authenticated user's profile.

    Email is intentionally excluded: it is the USERNAME_FIELD and unique,
    so changing it would require a re-verification flow that does not exist.
    """

    class Meta:
        model = User
        fields = ("name", "phone")

    def validate_name(self, value):
        value = value.strip()

        if len(value) < 2:
            raise serializers.ValidationError(
                "Name must contain at least 2 characters."
            )

        return value

    def validate_phone(self, value):
        if not value.isdigit():
            raise serializers.ValidationError(
                "Phone number must contain only digits."
            )

        if len(value) != 10:
            raise serializers.ValidationError(
                "Phone number must be exactly 10 digits."
            )

        if (
            User.objects.filter(phone=value)
            .exclude(id=self.instance.id)
            .exists()
        ):
            raise serializers.ValidationError(
                "Phone number already exists."
            )

        return value
