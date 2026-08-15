from django.contrib import admin

from .models import CropJob


@admin.register(CropJob)
class CropJobAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "page_count", "user", "created_at")
    list_filter = ("status",)
    readonly_fields = (
        "id", "user", "owner_token", "original_filename", "page_count",
        "cropped_pages", "crop_rect", "output_file", "status", "error_message",
        "created_at",
    )
