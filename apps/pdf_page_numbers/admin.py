from django.contrib import admin

from .models import PageNumberJob


@admin.register(PageNumberJob)
class PageNumberJobAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "position", "start_number", "page_count", "user", "created_at")
    list_filter = ("status", "position")
    readonly_fields = (
        "id", "user", "owner_token", "original_filename", "page_count",
        "numbered_pages", "start_number", "position", "font_size", "font_color",
        "margin", "prefix", "suffix", "output_file", "status", "error_message",
        "created_at",
    )
