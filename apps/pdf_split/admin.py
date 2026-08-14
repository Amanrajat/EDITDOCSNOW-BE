from django.contrib import admin

from .models import SplitJob


@admin.register(SplitJob)
class SplitJobAdmin(admin.ModelAdmin):
    list_display = ("id", "mode", "status", "source_pages", "output_count", "user", "created_at")
    list_filter = ("status", "mode")
    readonly_fields = (
        "id", "user", "owner_token", "source_filename", "source_pages", "mode", "params",
        "output_file", "is_zip", "output_filenames", "output_count",
        "status", "error_message", "created_at",
    )
