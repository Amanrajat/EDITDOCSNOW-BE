from django.contrib import admin

from .models import RotateJob


@admin.register(RotateJob)
class RotateJobAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "degrees", "page_count", "user", "created_at")
    list_filter = ("status", "degrees")
    readonly_fields = (
        "id", "user", "owner_token", "original_filename", "page_count",
        "rotated_pages", "degrees", "output_file", "status", "error_message",
        "created_at",
    )
