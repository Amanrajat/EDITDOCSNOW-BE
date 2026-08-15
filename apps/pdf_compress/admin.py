from django.contrib import admin

from .models import CompressJob


@admin.register(CompressJob)
class CompressJobAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "level", "original_size", "compressed_size", "page_count", "user", "created_at")
    list_filter = ("status", "level")
    readonly_fields = (
        "id", "user", "owner_token", "original_filename", "page_count",
        "level", "original_size", "compressed_size", "output_file", "status",
        "error_message", "created_at",
    )
