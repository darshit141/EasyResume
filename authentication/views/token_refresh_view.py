from rest_framework import status
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.views import TokenRefreshView

from authentication.serializers.token_refresh_serializer import SafeTokenRefreshSerializer
from authentication.utils.jwt_cookie import set_refresh_token_cookie


class SafeTokenRefreshView(TokenRefreshView):
    serializer_class = SafeTokenRefreshSerializer

    def post(self, request, *args, **kwargs):
        data = request.data.copy() if hasattr(request.data, "copy") else dict(request.data)
        # If refresh token was not in the body, read it from the HttpOnly cookie
        if not data.get("refresh"):
            cookie_token = request.COOKIES.get("refresh_token")
            if cookie_token:
                data["refresh"] = cookie_token

        serializer = self.get_serializer(data=data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as e:
            raise InvalidToken(e.args[0])

        response_data = serializer.validated_data
        response = Response(response_data, status=status.HTTP_200_OK)

        # Set the rotated refresh token as an HttpOnly, SameSite=Lax, Secure cookie
        new_refresh = response_data.get("refresh") or data.get("refresh")
        if new_refresh:
            set_refresh_token_cookie(response, new_refresh)

        return response
