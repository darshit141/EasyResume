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
    # Who takes the payment ("stripe" | "razorpay") and how the customer can
    # pay, so the page can say "Pay with UPI" without knowing the routing.
    processor = serializers.CharField(required=False)
    payment_methods = serializers.ListField(child=serializers.CharField(), required=False)
    # True for INR, whose prices include GST. `tax_note` is the exact line to
    # print under the price, worded from the live GST registration state.
    tax_inclusive = serializers.BooleanField(required=False)
    tax_note = serializers.CharField(required=False)


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


class RazorpayCheckoutSerializer(serializers.Serializer):
    """Exactly what Razorpay Checkout needs to open. `key_id` is Razorpay's
    public merchant identifier, not a secret."""

    key_id = serializers.CharField()
    subscription_id = serializers.CharField()
    name = serializers.CharField()
    description = serializers.CharField()
    amount = serializers.IntegerField()
    currency = serializers.CharField()
    prefill = serializers.DictField(child=serializers.CharField(allow_blank=True))


class CheckoutResponseSerializer(serializers.Serializer):
    """`processor` says which flow to run. For "stripe", redirect to
    `checkout_url`. For "razorpay", open Razorpay Checkout with `razorpay`;
    `checkout_url` is Razorpay's hosted fallback link for the same
    subscription, for a browser that cannot load Checkout."""

    processor = serializers.CharField(default="stripe")
    checkout_url = serializers.CharField(allow_blank=True)
    razorpay = RazorpayCheckoutSerializer(required=False)


class RazorpayConfirmSerializer(serializers.Serializer):
    razorpay_payment_id = serializers.CharField(max_length=64)
    razorpay_subscription_id = serializers.CharField(max_length=64)
    razorpay_signature = serializers.CharField(max_length=128)


class PortalResponseSerializer(serializers.Serializer):
    portal_url = serializers.URLField()


class SubscriptionStatusSerializer(serializers.Serializer):
    plan_id = serializers.CharField(allow_blank=True)
    status = serializers.CharField()
    is_active = serializers.BooleanField()
    current_period_end = serializers.DateTimeField(allow_null=True)
    # "stripe" | "razorpay" | "" — decides what "Manage billing" does, since
    # only Stripe has a hosted customer portal.
    payment_processor = serializers.CharField(allow_blank=True)
    currency = serializers.CharField(allow_blank=True)
    billing_period = serializers.CharField(allow_blank=True)
    cancel_at_period_end = serializers.BooleanField()


class EntitlementsSerializer(serializers.Serializer):
    """The documented shape of `EntitlementsService.usage_summary`.

    This serializer never touches the response — the view returns the dict
    directly — so its only job is to be the published contract in the
    OpenAPI schema. That makes drift silent and expensive: a client
    generated from the schema simply won't know about a field that exists,
    and `tests_entitlements.py` asserts the two stay in step.
    """

    plan_id = serializers.CharField()
    tailored_resumes_used = serializers.IntegerField()
    tailored_resumes_limit = serializers.IntegerField(allow_null=True)
    tailored_resumes_remaining = serializers.IntegerField(allow_null=True)
    ai_calls_used = serializers.IntegerField()
    ai_calls_limit = serializers.IntegerField(allow_null=True)
    ai_calls_remaining = serializers.IntegerField(allow_null=True)
    # Credits earned outside the plan (e.g. a rewarded ad). Already added
    # into the two limits above; reported separately so the UI can explain
    # why an allowance is larger than the plan's headline number.
    bonus_credits = serializers.IntegerField()
    allowed_ai_providers = serializers.ListField(child=serializers.CharField())
    ai_provider_selection_enabled = serializers.BooleanField()
    docx_export = serializers.BooleanField()
    tailored_resume_version_history = serializers.BooleanField()
    resume_template_customization = serializers.BooleanField()
    shows_ads = serializers.BooleanField()
    job_applications_used = serializers.IntegerField()
    job_applications_limit = serializers.IntegerField(allow_null=True)
    reply_tracking = serializers.ChoiceField(choices=["none", "alias", "gmail"])
    # True when the user supplied their own provider key, in which case the
    # AI-call ceiling above is reported as unlimited because it is no longer
    # enforced for them.
    uses_own_ai_key = serializers.BooleanField()


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


class UserAIKeyEntrySerializer(serializers.Serializer):
    """One provider's BYOK state. Note what is absent: the key. `masked_key`
    is the last four characters only, which is enough for a user to confirm
    *which* credential is saved and useless to anyone who intercepts it."""

    provider = serializers.CharField()
    label = serializers.CharField()
    configured = serializers.BooleanField()
    is_active = serializers.BooleanField()
    masked_key = serializers.CharField(allow_blank=True)
    last_validated_at = serializers.DateTimeField(allow_null=True)
    last_used_at = serializers.DateTimeField(allow_null=True)


class UserAIKeySummarySerializer(serializers.Serializer):
    enabled = serializers.BooleanField()
    supported_providers = serializers.ListField(child=serializers.CharField())
    keys = UserAIKeyEntrySerializer(many=True)


class UserAIKeyWriteSerializer(serializers.Serializer):
    provider = serializers.CharField(max_length=20)
    # write_only is not decoration here: it is what stops the key being
    # echoed back in a response or in a validation error, which is the most
    # common way a secret ends up in a browser's network log.
    api_key = serializers.CharField(max_length=400, trim_whitespace=True, write_only=True)


class UserAIKeyToggleSerializer(serializers.Serializer):
    provider = serializers.CharField(max_length=20)
    is_active = serializers.BooleanField()


class BillingProfileSerializer(serializers.Serializer):
    """The buyer details an Indian tax invoice needs.

    `state_code` is the place of supply. A GSTIN is optional; when given it
    must be well-formed, its state wins over the one chosen, and a legal
    name and address become required, because a business invoice without
    them cannot be used to claim input tax credit.
    """

    state_code = serializers.CharField(max_length=2, allow_blank=True, required=False)
    state_name = serializers.CharField(read_only=True)
    gstin = serializers.CharField(max_length=20, allow_blank=True, required=False)
    legal_name = serializers.CharField(max_length=200, allow_blank=True, required=False)
    address = serializers.CharField(max_length=500, allow_blank=True, required=False)

    def validate(self, attrs):
        from billing import gst

        gstin = gst.normalize_gstin(attrs.get("gstin", ""))
        state_code = (attrs.get("state_code") or "").strip()

        if gstin:
            try:
                gstin = gst.validate_gstin(gstin)
            except gst.GSTError as exc:
                raise serializers.ValidationError({"gstin": str(exc)})
            state_code = gst.gstin_state_code(gstin)
            errors = {}
            if not (attrs.get("legal_name") or "").strip():
                errors["legal_name"] = "Enter the business name registered against this GSTIN."
            if not (attrs.get("address") or "").strip():
                errors["address"] = "Enter the registered business address."
            if errors:
                raise serializers.ValidationError(errors)
        elif state_code and not gst.is_valid_state_code(state_code):
            raise serializers.ValidationError({"state_code": "Choose a state from the list."})

        attrs["gstin"] = gstin
        attrs["state_code"] = state_code
        attrs["legal_name"] = (attrs.get("legal_name") or "").strip()
        attrs["address"] = (attrs.get("address") or "").strip()
        return attrs


class GSTStateSerializer(serializers.Serializer):
    code = serializers.CharField()
    name = serializers.CharField()


class GSTInfoSerializer(serializers.Serializer):
    registered = serializers.BooleanField()
    rate_percent = serializers.CharField()
    states = GSTStateSerializer(many=True)


class InvoiceSummarySerializer(serializers.Serializer):
    id = serializers.IntegerField()
    number = serializers.CharField()
    kind = serializers.CharField()
    kind_label = serializers.CharField()
    issued_at = serializers.DateTimeField()
    description = serializers.CharField()
    currency = serializers.CharField()
    total = serializers.IntegerField()
    total_display = serializers.CharField()
    tax_total = serializers.IntegerField()
