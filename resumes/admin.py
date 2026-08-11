from django.contrib import admin
from .models import JobResumeCuration, Resume


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


@admin.register(JobResumeCuration)
class JobResumeCurationAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "job_title",
        "status",
        "error_code",
        "gap_analysis_error_code",
        "created_at",
    )

    search_fields = (
        "job_title",
        "user__email",
    )

    list_filter = (
        "status",
        "created_at",
    )

    readonly_fields = (
        "analysis_data",
        "candidate_data_snapshot",
        "gap_analysis_data",
        "created_at",
        "updated_at",
    )
