import uuid

from django.conf import settings
from django.db import models

from apps.common.storage import private_job_storage


class SplitJob(models.Model):
    """
    A single "split one PDF into N PDFs" operation. Synchronous today, same
    reasoning as MergeJob in apps.pdf_merge: a stable, unguessable id so a
    future background-job version doesn't need a breaking API change.
    """

    class Mode(models.TextChoices):
        ALL_PAGES = "all_pages", "Every page as its own PDF"
        RANGES = "ranges", "Custom page ranges"
        EVERY_N = "every_n", "Split every N pages"
        EXTRACT = "extract", "Extract specific pages into one PDF"

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="split_jobs",
        null=True,
        blank=True,
        db_index=True,
    )

    source_filename = models.CharField(max_length=255, blank=True, default="")
    source_pages = models.PositiveIntegerField(default=0)

    mode = models.CharField(max_length=20, choices=Mode.choices)
    params = models.JSONField(default=dict)

    # Bearer token issued once, at creation, in the create response only.
    # See apps.common.ownership for why: there are no user accounts yet, so
    # this is what makes a job private to whoever created it.
    owner_token = models.CharField(max_length=64, db_index=True, blank=True, default="")

    output_file = models.FileField(
        upload_to="splits/output/", storage=private_job_storage, null=True, blank=True,
    )
    is_zip = models.BooleanField(default=False)
    output_filenames = models.JSONField(default=list)
    output_count = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROCESSING,
        db_index=True,
    )
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "pdf_split_jobs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"SplitJob({self.id}, {self.mode}, {self.status})"
