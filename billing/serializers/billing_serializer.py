from rest_framework import serializers

from billing.plans import BILLING_PERIODS, PLANS_BY_ID, SUPPORTED_CURRENCIES


class OfferSerializer(serializers.Serializer):
    """The running promotion, if any. Null outside a sale window."""

    label = serializers.CharField()
    badge = serializers.CharField(allow_blank=True)
    ends_at = serializers.DateTimeField()


class PriceOptionSerializer(serializers.Serializer):
    amount_cents = serializers.IntegerField()
    display = serializers.CharField()
    purchasable = serializers.BooleanField()
    # `amount_cents` is always what will be charged. During a promotion
    # these carry the standard price so the card can strike it through;
    # outside one they are null.
    original_amount_cents = serializers.IntegerField(allow_null=True, required=False)
    original_display = serializers.CharField(allow_null=True, required=False)
    offer = OfferSerializer(allow_null=True, required=False)


class PlanSerializer(serializers.Serializer):
    """Represents one plan already resolved to a single currency — see
    PlanListAPIView, which resolves `?currency=` before building this. Free
    has `monthly`/`annual` both null (nothing to check out); Pro has both,
    each null only if that specific currency/period isn't configured yet."""

    id = serializers.CharField()
    name = serializers.CharField()
    features = serializers.ListField(child=serializers.CharField())
    currency = serializers.CharField()
    monthly = PriceOptionSerializer(allow_null=True)
    annual = PriceOptionSerializer(allow_null=True)


class CheckoutRequestSerializer(serializers.Serializer):
    plan_id = serializers.ChoiceField(choices=list(PLANS_BY_ID.keys()))
    currency = serializers.ChoiceField(choices=SUPPORTED_CURRENCIES, required=False)
    billing_period = serializers.ChoiceField(choices=BILLING_PERIODS, required=False, default="monthly")


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
    ai_provider_selection_enabled = serializers.BooleanField()
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
