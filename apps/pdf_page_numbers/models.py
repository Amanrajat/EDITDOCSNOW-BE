import uuid

from django.conf import settings
from django.db import models

from apps.common.storage import private_job_storage


class PageNumberJob(models.Model):
    """
    A single "stamp page numbers onto these pages" operation. Same shape
    as every other job model: synchronous, UUID-addressable, private via
    the shared owner_token layer (apps.common.ownership). The source file
    is never persisted.
    """

    class Position(models.TextChoices):
        TOP_LEFT = "top-left", "Top left"
        TOP_CENTER = "top-center", "Top center"
        TOP_RIGHT = "top-right", "Top right"
        BOTTOM_LEFT = "bottom-left", "Bottom left"
        BOTTOM_CENTER = "bottom-center", "Bottom center"
        BOTTOM_RIGHT = "bottom-right", "Bottom right"

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="page_number_jobs",
        null=True,
        blank=True,
        db_index=True,
    )

    owner_token = models.CharField(max_length=64, db_index=True, blank=True, default="")

    original_filename = models.CharField(max_length=255, blank=True, default="")
    page_count = models.PositiveIntegerField(default=0)

    # 1-based page numbers that were stamped (empty list request = every page).
    numbered_pages = models.JSONField(default=list)

    start_number = models.IntegerField(default=1)
    position = models.CharField(max_length=20, choices=Position.choices, default=Position.BOTTOM_CENTER)
    font_size = models.PositiveIntegerField(default=12)
    font_color = models.CharField(max_length=7, default="#000000")
    margin = models.FloatField(default=28.0)
    prefix = models.CharField(max_length=40, blank=True, default="")
    suffix = models.CharField(max_length=40, blank=True, default="")

    output_file = models.FileField(
        upload_to="page-numbers/output/", storage=private_job_storage, null=True, blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROCESSING,
        db_index=True,
    )
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "pdf_page_number_jobs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"PageNumberJob({self.id}, {self.position}, {self.status})"
