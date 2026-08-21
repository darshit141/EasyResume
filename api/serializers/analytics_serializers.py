from rest_framework import serializers

from api.models import AnalyticsEvent

# Only UTM parameters are accepted — this is a funnel counter, not a
# general-purpose event-properties store a client could stuff anything
# into.
_ALLOWED_METADATA_KEYS = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"}


class TrackEventRequestSerializer(serializers.Serializer):
    event_type = serializers.ChoiceField(choices=AnalyticsEvent.EVENT_CHOICES)
    session_id = serializers.CharField(max_length=64, required=False, allow_blank=True)
    metadata = serializers.DictField(required=False)

    def validate_metadata(self, value):
        cleaned = {
            key: str(val)[:200]
            for key, val in value.items()
            if key in _ALLOWED_METADATA_KEYS and val is not None
        }
        return cleaned
