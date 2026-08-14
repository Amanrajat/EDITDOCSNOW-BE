from django.contrib import admin

from .models import OrganizeJob


@admin.register(OrganizeJob)
class OrganizeJobAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "page_count", "user", "created_at")
    list_filter = ("status",)
    readonly_fields = (
        "id", "user", "owner_token", "original_filename", "page_count",
        "page_order", "output_file", "status", "error_message", "created_at",
    )
