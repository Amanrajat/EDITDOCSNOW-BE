from django.contrib import admin

from .models import BatchFileJob, BatchJob


class BatchFileJobInline(admin.TabularInline):
    model = BatchFileJob
    extra = 0
    readonly_fields = (
        "id", "order", "original_filename", "status", "error_message",
        "page_count", "original_size", "compressed_size", "created_at",
    )
    can_delete = False


@admin.register(BatchJob)
class BatchJobAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "operation", "total_files", "user", "created_at")
    list_filter = ("status", "operation")
    readonly_fields = (
        "id", "user", "owner_token", "operation", "options", "total_files",
        "output_zip", "status", "created_at", "completed_at",
    )
    inlines = [BatchFileJobInline]
