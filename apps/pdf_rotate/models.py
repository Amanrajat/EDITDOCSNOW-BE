import uuid

from django.conf import settings
from django.db import models

from apps.common.storage import private_job_storage


class RotateJob(models.Model):
    """
    A single "rotate these pages by this many degrees" operation. Same
    shape/reasoning as the other job models: synchronous, UUID-addressable,
    private via the shared owner_token layer (apps.common.ownership). The
    source file is never persisted.
    """

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="rotate_jobs",
        null=True,
        blank=True,
        db_index=True,
    )

    owner_token = models.CharField(max_length=64, db_index=True, blank=True, default="")

    original_filename = models.CharField(max_length=255, blank=True, default="")
    page_count = models.PositiveIntegerField(default=0)

    # 1-based page numbers that were rotated (empty list request = all pages).
    rotated_pages = models.JSONField(default=list)
    degrees = models.IntegerField(default=0)

    output_file = models.FileField(
        upload_to="rotate/output/", storage=private_job_storage, null=True, blank=True,
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
        db_table = "pdf_rotate_jobs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"RotateJob({self.id}, {self.degrees}deg, {self.status})"
