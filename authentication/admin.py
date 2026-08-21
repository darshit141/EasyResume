from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import CookieConsent, PasswordResetOTP, PolicyAcceptance, User


@admin.register(User)
class CustomUserAdmin(UserAdmin):
    list_display = (
        "email",
        "name",
        "phone",
        "is_staff",
        "is_active",
    )

    ordering = ("email",)

    search_fields = (
        "email",
        "name",
        "phone",
    )

    readonly_fields = (
        "last_login",
        "date_joined",
    )

    fieldsets = (
        (None, {
            "fields": (
                "email",
                "password",
            )
        }),
        ("Personal Info", {
            "fields": (
                "name",
                "phone",
            )
        }),
        ("Permissions", {
            "fields": (
                "is_active",
                "is_staff",
                "is_superuser",
                "groups",
                "user_permissions",
            )
        }),
        ("Important Dates", {
            "fields": (
                "last_login",
                "date_joined",
            )
        }),
    )

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "email",
                    "name",
                    "phone",
                    "password1",
                    "password2",
                    "is_staff",
                    "is_superuser",
                ),
            },
        ),
    )


@admin.register(PasswordResetOTP)
class PasswordResetOTPAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "otp",
        "is_verified",
        "expires_at",
        "created_at",
    )

    search_fields = (
        "user__email",
        "otp",
    )

    list_filter = (
        "is_verified",
    )

    ordering = (
        "-created_at",
    )


@admin.register(PolicyAcceptance)
class PolicyAcceptanceAdmin(admin.ModelAdmin):
    list_display = ("user", "policy_type", "version", "accepted_at")
    list_filter = ("policy_type", "version")
    search_fields = ("user__email",)
    readonly_fields = [field.name for field in PolicyAcceptance._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(CookieConsent)
class CookieConsentAdmin(admin.ModelAdmin):
    list_display = ("user", "analytics", "marketing", "consent_version", "updated_at")
    search_fields = ("user__email",)
    readonly_fields = ("user", "created_at", "updated_at")