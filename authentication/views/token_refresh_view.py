from rest_framework_simplejwt.views import TokenRefreshView
from authentication.serializers.token_refresh_serializer import SafeTokenRefreshSerializer

class SafeTokenRefreshView(TokenRefreshView):
    serializer_class = SafeTokenRefreshSerializer
