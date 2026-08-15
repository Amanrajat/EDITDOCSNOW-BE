from django.contrib import admin

from .models import OcrJob


@admin.register(OcrJob)
class OcrJobAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "language", "page_count", "ocr_page_count", "user", "created_at")
    list_filter = ("status", "language")
    readonly_fields = (
        "id", "user", "owner_token", "original_filename", "language",
        "source_file", "output_file", "page_count", "ocr_page_count",
        "status", "error_message", "created_at", "completed_at",
    )
