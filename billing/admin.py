from django.contrib import admin

from billing.models import (
    AIUsageLog,
    BillingProfile,
    Coupon,
    CouponRedemption,
    GSTInvoice,
    PlanPrice,
    PricingOffer,
    RazorpayWebhookEvent,
    StripeWebhookEvent,
    Subscription,
)


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan_id", "status", "payment_processor", "currency", "current_period_end", "updated_at")
    list_filter = ("plan_id", "status", "payment_processor", "currency")
    search_fields = ("user__email",)
    readonly_fields = ("created_at", "updated_at")


@admin.register(AIUsageLog)
class AIUsageLogAdmin(admin.ModelAdmin):
    list_display = ("user", "action", "provider", "created_at")
    list_filter = ("action", "provider", "created_at")
    search_fields = ("user__email",)
    readonly_fields = ("user", "action", "provider", "created_at")

    def has_add_permission(self, request):
        # Usage rows only ever come from real AI calls the server logged —
        # never hand-created, or the counts stop meaning anything.
        return False


@admin.register(Coupon)
class CouponAdmin(admin.ModelAdmin):
    list_display = ("code", "duration_days", "max_redemptions", "redemptions_used", "is_active", "created_at")
    list_filter = ("is_active",)
    search_fields = ("code", "description")
    readonly_fields = ("created_at",)

    @admin.display(description="Used")
    def redemptions_used(self, coupon):
        return f"{coupon.redemptions_used} / {coupon.max_redemptions}"


@admin.register(CouponRedemption)
class CouponRedemptionAdmin(admin.ModelAdmin):
    list_display = ("coupon", "user", "redeemed_at")
    list_filter = ("coupon",)
    search_fields = ("user__email", "coupon__code")
    readonly_fields = ("coupon", "user", "redeemed_at")

    def has_add_permission(self, request):
        # Redemptions only ever come from a real redeem call — never
        # hand-created, or the "used once" guarantee stops meaning anything.
        return False


@admin.register(StripeWebhookEvent)
class StripeWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "stripe_event_id", "processed_at", "error", "created_at")
    list_filter = ("event_type",)
    search_fields = ("stripe_event_id",)
    readonly_fields = [field.name for field in StripeWebhookEvent._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(PlanPrice)
class PlanPriceAdmin(admin.ModelAdmin):
    list_display = ("plan_id", "currency", "billing_period", "amount_cents", "purchasable", "is_active", "updated_at")
    list_filter = ("plan_id", "currency", "billing_period", "is_active")
    readonly_fields = ("updated_at",)

    @admin.display(boolean=True, description="Purchasable")
    def purchasable(self, price):
        # A price with no processor id displays but cannot be bought - worth
        # seeing at a glance rather than discovering at checkout. INR is
        # charged through a Razorpay Plan, everything else a Stripe Price.
        from billing.plans import PROCESSOR_RAZORPAY, processor_for

        if processor_for(price.currency) == PROCESSOR_RAZORPAY:
            return bool(price.razorpay_plan_id)
        return bool(price.stripe_price_id)


@admin.register(PricingOffer)
class PricingOfferAdmin(admin.ModelAdmin):
    list_display = (
        "campaign", "label", "plan_id", "currency", "billing_period",
        "amount_cents", "starts_at", "ends_at", "live_now",
    )
    list_filter = ("campaign", "plan_id", "currency", "billing_period", "is_active")
    search_fields = ("campaign", "label")
    readonly_fields = ("created_at",)

    @admin.display(boolean=True, description="Live now")
    def live_now(self, offer):
        """Whether customers are actually seeing this right now - which is
        not the same as is_active, because the dates and the Stripe price
        both have to line up too."""
        return offer.is_live()


@admin.register(RazorpayWebhookEvent)
class RazorpayWebhookEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "event_id", "processed_at", "error", "created_at")
    list_filter = ("event_type",)
    search_fields = ("event_id",)
    readonly_fields = [field.name for field in RazorpayWebhookEvent._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(BillingProfile)
class BillingProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "state_code", "gstin", "legal_name", "updated_at")
    search_fields = ("user__email", "gstin", "legal_name")


@admin.register(GSTInvoice)
class GSTInvoiceAdmin(admin.ModelAdmin):
    """Read-only on purpose. An issued invoice is a legal record: a mistake
    is corrected with a credit note, never by editing the original."""

    list_display = ("number", "kind", "issued_at", "buyer_email", "place_of_supply_name", "total_rupees")
    list_filter = ("kind", "financial_year", "supply_type")
    search_fields = ("number", "buyer_email", "buyer_gstin", "razorpay_payment_id")
    readonly_fields = [field.name for field in GSTInvoice._meta.fields]

    @admin.display(description="Total (INR)")
    def total_rupees(self, invoice):
        from billing.gst import rupees

        return rupees(invoice.total)

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
