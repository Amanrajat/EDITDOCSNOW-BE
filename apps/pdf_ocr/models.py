import uuid

from django.conf import settings
from django.db import models

from apps.common.storage import private_job_storage


class OcrJob(models.Model):
    """
    A single "make this scanned PDF searchable" operation, processed
    asynchronously via Celery - OCR is genuinely long-running (multi-page
    scans can take tens of seconds to minutes), matching Phase 9's
    guidance to reserve background workers for exactly this kind of work
    (see apps.pdf_batch for the other Celery-backed feature and its own
    reasoning). Same ownership-token security model as every other job.
    The source file is temporary (deleted once processed); only the
    resulting searchable PDF is kept.
    """

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ocr_jobs",
        null=True,
        blank=True,
        db_index=True,
    )

    owner_token = models.CharField(max_length=64, db_index=True, blank=True, default="")

    original_filename = models.CharField(max_length=255, blank=True, default="")
    language = models.CharField(max_length=40, default="eng")

    source_file = models.FileField(
        upload_to="ocr/source/", storage=private_job_storage, null=True, blank=True,
    )
    output_file = models.FileField(
        upload_to="ocr/output/", storage=private_job_storage, null=True, blank=True,
    )

    page_count = models.PositiveIntegerField(default=0)
    ocr_page_count = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "pdf_ocr_jobs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"OcrJob({self.id}, {self.language}, {self.status})"
