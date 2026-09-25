from django.contrib import admin

from inbox.models import InboundEmail, ReplyAddress, TrackingAddress


@admin.register(InboundEmail)
class InboundEmailAdmin(admin.ModelAdmin):
    list_display = ("received_at", "user", "from_address", "subject", "category", "curation", "state")
    list_filter = ("state", "category", "route", "classification_source", "status_applied_automatically")
    search_fields = ("from_address", "subject", "user__email")
    exclude = ("raw_mime", "text_body")
    readonly_fields = [f.name for f in InboundEmail._meta.fields if f.name not in ("raw_mime", "text_body")]


@admin.register(TrackingAddress)
class TrackingAddressAdmin(admin.ModelAdmin):
    list_display = ("user", "token", "first_email_received_at", "created_at")
    search_fields = ("user__email", "token")
    readonly_fields = ("token", "created_at", "rotated_at")


@admin.register(ReplyAddress)
class ReplyAddressAdmin(admin.ModelAdmin):
    list_display = ("application", "token", "created_at")
    readonly_fields = ("token", "created_at")
