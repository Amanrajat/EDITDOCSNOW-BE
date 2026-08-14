from django.contrib import admin

from .models import RemovePagesJob


@admin.register(RemovePagesJob)
class RemovePagesJobAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "source_page_count", "output_page_count", "user", "created_at")
    list_filter = ("status",)
    readonly_fields = (
        "id", "user", "owner_token", "original_filename", "source_page_count",
        "removed_pages", "output_page_count", "output_file", "status",
        "error_message", "created_at",
    )
