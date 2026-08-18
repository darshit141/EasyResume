from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import RefreshToken


class LogoutService:
    """
    Service for logging out by blacklisting the submitted refresh token, so
    it (and any access token minted from it) can no longer be used to
    authenticate once the user has explicitly logged out.
    """

    @staticmethod
    def logout(refresh_token):
        try:
            token = RefreshToken(refresh_token)
            token.blacklist()
        except TokenError:
            return {
                "success": False,
                "message": "Invalid or already-invalidated refresh token.",
            }

        return {
            "success": True,
            "message": "Logged out successfully.",
        }
