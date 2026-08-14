import uuid

from django.conf import settings
from django.db import models

from apps.common.storage import private_job_storage


class MergeJob(models.Model):
    """
    A single "merge N PDFs into one" operation. Processing is synchronous
    today (the API request only returns once the merge is done), but this
    model exists so results are addressable by a stable, unguessable id -
    consistent with Document in apps.docs_editor - and so a future
    background-job version of this feature (Celery) can reuse the same
    shape without a breaking API change.
    """

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="merge_jobs",
        null=True,
        blank=True,
        db_index=True,
    )

    output_file = models.FileField(
        upload_to="merges/output/",
        storage=private_job_storage,
        null=True,
        blank=True,
    )

    # Bearer token issued once, at creation, in the create response only.
    # See apps.common.ownership for why: there are no user accounts yet, so
    # this is what makes a job private to whoever created it.
    owner_token = models.CharField(max_length=64, db_index=True, blank=True, default="")

    source_filenames = models.JSONField(default=list)
    source_count = models.PositiveIntegerField(default=0)
    total_pages = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROCESSING,
        db_index=True,
    )
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "pdf_merge_jobs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"MergeJob({self.id}, {self.status})"
