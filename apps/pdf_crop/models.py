import uuid

from django.conf import settings
from django.db import models

from apps.common.storage import private_job_storage


class CropJob(models.Model):
    """
    A single "crop these pages to this rectangle" operation. Same shape as
    every other job model: synchronous, UUID-addressable, private via the
    shared owner_token layer (apps.common.ownership). The source file is
    never persisted.

    The crop rectangle is stored as fractions (0..1) of each target page's
    own box, top-left origin, y increasing downward - the same convention
    PyMuPDF's Page.rect uses, and the natural one for a browser-based crop
    editor (canvas/image pixel coordinates). Storing fractions rather than
    absolute PDF points is what lets one rectangle apply correctly across
    pages of different sizes in the same document.
    """

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="crop_jobs",
        null=True,
        blank=True,
        db_index=True,
    )

    owner_token = models.CharField(max_length=64, db_index=True, blank=True, default="")

    original_filename = models.CharField(max_length=255, blank=True, default="")
    page_count = models.PositiveIntegerField(default=0)

    # 1-based page numbers that were cropped (empty list request = all pages).
    cropped_pages = models.JSONField(default=list)
    # Fractional crop rect actually applied: {"x0":.., "y0":.., "x1":.., "y1":..}
    crop_rect = models.JSONField(default=dict)

    output_file = models.FileField(
        upload_to="crop/output/", storage=private_job_storage, null=True, blank=True,
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
        db_table = "pdf_crop_jobs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"CropJob({self.id}, {self.crop_rect}, {self.status})"
