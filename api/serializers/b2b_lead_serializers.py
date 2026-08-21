from rest_framework import serializers

from api.models import B2BLead


class B2BLeadRequestSerializer(serializers.ModelSerializer):
    class Meta:
        model = B2BLead
        fields = ["name", "email", "company_name", "team_size", "message"]


class B2BLeadAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = B2BLead
        fields = [
            "id", "name", "email", "company_name", "team_size", "message",
            "contacted", "created_at",
        ]
        read_only_fields = fields
