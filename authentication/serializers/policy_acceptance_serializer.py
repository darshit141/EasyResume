from rest_framework import serializers

from authentication.models import PolicyAcceptance


class PolicyAcceptanceRequestSerializer(serializers.Serializer):
    policy_type = serializers.ChoiceField(choices=PolicyAcceptance.POLICY_TYPE_CHOICES)


class PolicyAcceptanceSerializer(serializers.ModelSerializer):
    class Meta:
        model = PolicyAcceptance
        fields = ["policy_type", "version", "accepted_at"]
        read_only_fields = fields
