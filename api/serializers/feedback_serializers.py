from rest_framework import serializers


class FeedbackRequestSerializer(serializers.Serializer):
    CATEGORY_CHOICES = [("bug", "Bug"), ("feature", "Feature request")]

    category = serializers.ChoiceField(choices=CATEGORY_CHOICES)
    message = serializers.CharField(max_length=4000, trim_whitespace=True)
    # Only required for an anonymous reporter — a signed-in user's account
    # email is used instead, since it's already verified.
    email = serializers.EmailField(required=False, allow_blank=True)
    page_url = serializers.CharField(max_length=500, required=False, allow_blank=True)

    def validate_message(self, value):
        value = value.strip()
        if not value:
            raise serializers.ValidationError("Please describe the bug or feature.")
        return value
