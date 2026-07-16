from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from .models import User, PasswordResetOTP


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