from rest_framework import serializers

from authentication.models import CookieConsent


class CookieConsentRequestSerializer(serializers.Serializer):
    # "necessary" is deliberately not a field here — it is never actually
    # optional (the app can't function without an auth cookie/token), so
    # there is nothing meaningful for the client to submit for it.
    analytics = serializers.BooleanField(default=False)
    marketing = serializers.BooleanField(default=False)


class CookieConsentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CookieConsent
        fields = ["analytics", "marketing", "consent_version", "updated_at"]
        read_only_fields = fields
