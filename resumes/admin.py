from django.contrib import admin
from .models import Resume


@admin.register(Resume)
class ResumeAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "original_filename",
        "status",
        "ats_score",
        "created_at",
    )

    search_fields = (
        "original_filename",
        "user__email",
    )

    list_filter = (
        "status",
        "created_at",
    )

    readonly_fields = (
        "parsed_data",
        "created_at",
        "updated_at",
    )