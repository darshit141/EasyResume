from django.contrib import admin

from api.models import AdminAuditLog, AnalyticsEvent, B2BLead


@admin.register(AdminAuditLog)
class AdminAuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor_email", "action", "target", "ip_address")
    list_filter = ("action",)
    search_fields = ("actor_email", "target")
    readonly_fields = [field.name for field in AdminAuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # An audit trail that admins can delete rows from isn't one.
        return False


@admin.register(AnalyticsEvent)
class AnalyticsEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "user", "session_id", "created_at")
    list_filter = ("event_type",)
    search_fields = ("user__email", "session_id")
    readonly_fields = [field.name for field in AnalyticsEvent._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(B2BLead)
class B2BLeadAdmin(admin.ModelAdmin):
    list_display = ("name", "email", "company_name", "team_size", "contacted", "created_at")
    list_filter = ("contacted", "team_size")
    search_fields = ("name", "email", "company_name")
    # `contacted` is the one field a staff member actually needs to change
    # (sales triage) — everything else is what the lead themselves
    # submitted and shouldn't be edited out from under them.
    readonly_fields = ("name", "email", "company_name", "team_size", "message", "created_at")

    def has_add_permission(self, request):
        return False
