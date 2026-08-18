from django.contrib import admin

from billing.models import AIUsageLog, Coupon, CouponRedemption, Subscription


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ("user", "plan_id", "status", "current_period_end", "updated_at")
    list_filter = ("plan_id", "status")
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
