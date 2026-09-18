from rest_framework import serializers
from authentication.models import User
from api.models import AdminAuditLog, SystemConfiguration
from billing.models import Subscription


class SystemConfigurationSerializer(serializers.ModelSerializer):
    is_custom = serializers.BooleanField(read_only=True, default=True)

    class Meta:
        model = SystemConfiguration
        fields = ["key", "value", "description", "category", "value_type", "updated_at", "is_custom"]
        read_only_fields = ["updated_at", "is_custom"]


class AdminAuditLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AdminAuditLog
        fields = ["id", "actor_email", "action", "target", "detail", "ip_address", "created_at"]
        read_only_fields = fields


class AdminUserSerializer(serializers.ModelSerializer):
    resume_count = serializers.IntegerField(read_only=True)
    subscription_status = serializers.SerializerMethodField()
    subscription_plan = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "name",
            "email",
            "phone",
            "is_active",
            "is_staff",
            "date_joined",
            "resume_count",
            "subscription_status",
            "subscription_plan",
        ]
        read_only_fields = fields

    def get_subscription_status(self, obj):
        try:
            sub = obj.subscription
            if sub.is_active:
                return "active"
            return sub.status
        except Subscription.DoesNotExist:
            return "none"

    def get_subscription_plan(self, obj):
        try:
            sub = obj.subscription
            if sub.is_active:
                return sub.plan_id
            return "none"
        except Subscription.DoesNotExist:
            return "none"
