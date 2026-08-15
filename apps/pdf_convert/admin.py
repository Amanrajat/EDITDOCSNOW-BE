from django.contrib import admin

from .models import ConversionJob


@admin.register(ConversionJob)
class ConversionJobAdmin(admin.ModelAdmin):
    list_display = ("id", "operation", "status", "source_filename", "user", "created_at")
    list_filter = ("status", "operation")
    readonly_fields = (
        "id", "user", "owner_token", "operation", "source_filename",
        "output_file", "output_is_zip", "metadata", "status", "error_message",
        "created_at",
    )
