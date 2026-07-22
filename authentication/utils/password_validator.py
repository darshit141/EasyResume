import re

from rest_framework import serializers

import string

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 20
SPECIAL_CHARACTERS = string.punctuation


def validate_password_strength(password):
    """
    Validate password strength.
    """

    if len(password) < MIN_PASSWORD_LENGTH:
        raise serializers.ValidationError(
            f"Password must be at least {MIN_PASSWORD_LENGTH} characters long."
        )

    if len(password) > MAX_PASSWORD_LENGTH:
        raise serializers.ValidationError(
            f"Password must not exceed {MAX_PASSWORD_LENGTH} characters."
        )

    if not re.search(r"[A-Z]", password):
        raise serializers.ValidationError(
            "Password must contain at least one uppercase letter."
        )

    if not re.search(r"[a-z]", password):
        raise serializers.ValidationError(
            "Password must contain at least one lowercase letter."
        )

    if not re.search(r"\d", password):
        raise serializers.ValidationError(
            "Password must contain at least one number."
        )

    if not any(char in SPECIAL_CHARACTERS  for char in password):
        raise serializers.ValidationError(
            "Password must contain at least one special character."
        )

    return password