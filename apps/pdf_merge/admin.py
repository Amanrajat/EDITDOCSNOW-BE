from django.contrib import admin

from .models import MergeJob


@admin.register(MergeJob)
class MergeJobAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "source_count", "total_pages", "user", "created_at")
    list_filter = ("status",)
    readonly_fields = (
        "id", "user", "owner_token", "output_file", "source_filenames", "source_count",
        "total_pages", "status", "error_message", "created_at",
    )
