from rest_framework import serializers

from billing.models import Coupon


class CouponSerializer(serializers.ModelSerializer):
    redemptions_used = serializers.ReadOnlyField()
    redemptions_remaining = serializers.ReadOnlyField()

    class Meta:
        model = Coupon
        fields = [
            "id", "code", "description", "duration_days", "max_redemptions",
            "is_active", "created_at", "redemptions_used", "redemptions_remaining",
        ]
        read_only_fields = ["id", "created_at"]


class CouponCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Coupon
        fields = ["code", "description", "duration_days", "max_redemptions"]

    def validate_code(self, value):
        code = value.strip().upper()
        if not code:
            raise serializers.ValidationError("Code must not be empty.")
        if Coupon.objects.filter(code=code).exists():
            raise serializers.ValidationError("A coupon with this code already exists.")
        return code
