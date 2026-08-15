import uuid

from django.conf import settings
from django.db import models

from apps.common.storage import private_job_storage


class BatchJob(models.Model):
    """
    A batch of same-operation PDF jobs (e.g. "compress these 10 files"),
    processed asynchronously via Celery - unlike every synchronous
    single-file PDF app, a batch is genuinely long-running/high-volume
    enough to warrant a background worker + pollable status (see
    core/celery.py and Phase 9 of the project spec). Same ownership-token
    security model as every other job (apps.common.ownership).
    """

    class Operation(models.TextChoices):
        COMPRESS = "compress", "Compress"

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        PARTIAL = "partial", "Partial (some files failed)"
        FAILED = "failed", "Failed (all files failed)"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="batch_jobs",
        null=True,
        blank=True,
        db_index=True,
    )

    owner_token = models.CharField(max_length=64, db_index=True, blank=True, default="")

    operation = models.CharField(max_length=20, choices=Operation.choices, default=Operation.COMPRESS)
    options = models.JSONField(default=dict)

    total_files = models.PositiveIntegerField(default=0)

    output_zip = models.FileField(
        upload_to="batch/output/", storage=private_job_storage, null=True, blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "pdf_batch_jobs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"BatchJob({self.id}, {self.operation}, {self.status})"


class BatchFileJob(models.Model):
    """One file within a BatchJob. Its source file is temporary (deleted
    once processed, success or fail) - only the output is kept until
    cleanup_batch_jobs removes the whole batch."""

    class Status(models.TextChoices):
        QUEUED = "queued", "Queued"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    batch = models.ForeignKey(BatchJob, on_delete=models.CASCADE, related_name="files")
    order = models.PositiveIntegerField(default=0)

    original_filename = models.CharField(max_length=255, blank=True, default="")
    source_file = models.FileField(
        upload_to="batch/source/", storage=private_job_storage, null=True, blank=True,
    )
    output_file = models.FileField(
        upload_to="batch/output/", storage=private_job_storage, null=True, blank=True,
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.QUEUED,
        db_index=True,
    )
    error_message = models.TextField(blank=True, default="")

    page_count = models.PositiveIntegerField(default=0)
    original_size = models.PositiveIntegerField(default=0)
    compressed_size = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "pdf_batch_file_jobs"
        ordering = ["order"]
        indexes = [
            models.Index(fields=["batch"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"BatchFileJob({self.id}, {self.original_filename}, {self.status})"
