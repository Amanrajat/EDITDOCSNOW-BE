import uuid

from django.conf import settings
from django.db import models

from apps.common.storage import private_job_storage


class OrganizeJob(models.Model):
    """
    A single "reorder this PDF's pages" operation. Same shape/reasoning as
    MergeJob/SplitJob: synchronous today, UUID-addressable, private via the
    shared owner_token layer (apps.common.ownership).

    The *source* file is never persisted - it's only needed transiently to
    build the reordered output (same pattern as Merge/Split, avoiding
    unbounded storage growth for files nobody will re-fetch).
    """

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="organize_jobs",
        null=True,
        blank=True,
        db_index=True,
    )

    # Bearer token issued once, at creation, in the create response only.
    # See apps.common.ownership for why: there are no user accounts yet, so
    # this is what makes a job private to whoever created it.
    owner_token = models.CharField(max_length=64, db_index=True, blank=True, default="")

    original_filename = models.CharField(max_length=255, blank=True, default="")
    page_count = models.PositiveIntegerField(default=0)

    # The validated order that was actually applied, 1-based page numbers,
    # e.g. [3, 1, 5, 2, 4] - kept for audit/debugging, not re-used on read.
    page_order = models.JSONField(default=list)

    output_file = models.FileField(
        upload_to="organize/output/", storage=private_job_storage, null=True, blank=True,
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
        db_table = "pdf_organize_jobs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"OrganizeJob({self.id}, {self.status})"
