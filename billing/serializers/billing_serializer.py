from rest_framework import serializers

from billing.plans import PLANS_BY_ID


class PlanSerializer(serializers.Serializer):
    id = serializers.CharField()
    name = serializers.CharField()
    price_display = serializers.CharField()
    price_cents = serializers.IntegerField()
    features = serializers.ListField(child=serializers.CharField())
    purchasable = serializers.SerializerMethodField()

    def get_purchasable(self, plan):
        return bool(plan.get("stripe_price_id"))


class CheckoutRequestSerializer(serializers.Serializer):
    plan_id = serializers.ChoiceField(choices=list(PLANS_BY_ID.keys()))


class CheckoutResponseSerializer(serializers.Serializer):
    checkout_url = serializers.URLField()


class PortalResponseSerializer(serializers.Serializer):
    portal_url = serializers.URLField()


class SubscriptionStatusSerializer(serializers.Serializer):
    plan_id = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    is_active = serializers.BooleanField()
    current_period_end = serializers.DateTimeField(allow_null=True)


class EntitlementsSerializer(serializers.Serializer):
    plan_id = serializers.CharField()
    tailored_resumes_used = serializers.IntegerField()
    tailored_resumes_limit = serializers.IntegerField(allow_null=True)
    tailored_resumes_remaining = serializers.IntegerField(allow_null=True)
    ai_calls_used = serializers.IntegerField()
    ai_calls_limit = serializers.IntegerField(allow_null=True)
    ai_calls_remaining = serializers.IntegerField(allow_null=True)
    allowed_ai_providers = serializers.ListField(child=serializers.CharField())
    docx_export = serializers.BooleanField()
    tailored_resume_version_history = serializers.BooleanField()
    shows_ads = serializers.BooleanField()


class BillingErrorSerializer(serializers.Serializer):
    detail = serializers.CharField()
    code = serializers.CharField()


class CouponRedeemRequestSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=40)


class CouponRedeemResponseSerializer(serializers.Serializer):
    plan_id = serializers.CharField()
    status = serializers.CharField()
    is_active = serializers.BooleanField()
    current_period_end = serializers.DateTimeField(allow_null=True)
