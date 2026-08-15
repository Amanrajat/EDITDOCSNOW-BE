import uuid

from django.conf import settings
from django.db import models

from apps.common.storage import private_job_storage


class CompressJob(models.Model):
    """
    A single "compress this PDF" operation. Same shape as every other job
    model: synchronous, UUID-addressable, private via the shared
    owner_token layer (apps.common.ownership). The source file is never
    persisted - only its size, for the before/after statistics.
    """

    class Level(models.TextChoices):
        HIGH_QUALITY = "high_quality", "High quality"
        RECOMMENDED = "recommended", "Recommended"
        HIGH_COMPRESSION = "high_compression", "High compression"
        MAXIMUM_COMPRESSION = "maximum_compression", "Maximum compression"

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="compress_jobs",
        null=True,
        blank=True,
        db_index=True,
    )

    owner_token = models.CharField(max_length=64, db_index=True, blank=True, default="")

    original_filename = models.CharField(max_length=255, blank=True, default="")
    page_count = models.PositiveIntegerField(default=0)

    level = models.CharField(max_length=24, choices=Level.choices, default=Level.RECOMMENDED)
    original_size = models.PositiveIntegerField(default=0)
    compressed_size = models.PositiveIntegerField(default=0)

    output_file = models.FileField(
        upload_to="compress/output/", storage=private_job_storage, null=True, blank=True,
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
        db_table = "pdf_compress_jobs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    @property
    def saved_size(self):
        return max(0, self.original_size - self.compressed_size)

    @property
    def reduction_percent(self):
        if not self.original_size:
            return 0.0
        return round((self.saved_size / self.original_size) * 100, 1)

    def __str__(self):
        return f"CompressJob({self.id}, {self.level}, {self.status})"
