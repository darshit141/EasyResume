from typing import Any
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework.exceptions import AuthenticationFailed

class SafeTokenRefreshSerializer(TokenRefreshSerializer):
    def validate(self, attrs: dict[str, Any]) -> dict[str, str]:
        try:
            return super().validate(attrs)
        except get_user_model().DoesNotExist:
            raise AuthenticationFailed(
                self.error_messages.get("no_active_account", "No active account found for the given token."),
                "no_active_account",
            )
