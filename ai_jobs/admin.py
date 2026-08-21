from django.contrib import admin

from ai_jobs.models import AIJob


@admin.register(AIJob)
class AIJobAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "job_type", "status", "provider", "created_at", "completed_at")
    list_filter = ("job_type", "status", "provider")
    search_fields = ("user__email", "error_code")
    readonly_fields = [field.name for field in AIJob._meta.fields]
