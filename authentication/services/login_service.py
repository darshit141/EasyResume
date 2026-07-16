from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken


class LoginService:
    """
    Service for user authentication.
    """

    @staticmethod
    def login_user(email, password):
        """
        Authenticate user and generate JWT tokens.
        """

        user = authenticate(
            email=email,
            password=password,
        )

        if user is None:
            return None

        refresh = RefreshToken.for_user(user)

        return {
            "user": user,
            "access": str(refresh.access_token),
            "refresh": str(refresh),
        }