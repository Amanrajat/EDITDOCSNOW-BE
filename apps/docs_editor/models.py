from django.db import models
from django.contrib.auth import get_user_model
import uuid

User = get_user_model()


class Document(models.Model):

    class Status(models.TextChoices):
        UPLOADED = "uploaded", "Uploaded"
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    class FileType(models.TextChoices):
        PDF = "pdf", "PDF"
        DOCX = "docx", "DOCX"
        XLSX = "xlsx", "XLSX"

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="documents",
        null=True,
        blank=True,
        db_index=True,
    )

    original_file = models.FileField(
        upload_to="documents/original/"
    )

    edited_file = models.FileField(
        upload_to="documents/edited/",
        null=True,
        blank=True,
    )

    original_name = models.CharField(
        max_length=255
    )

    file_type = models.CharField(
        max_length=10,
        choices=FileType.choices,
        default=FileType.PDF,
        db_index=True,
    )

    file_size = models.BigIntegerField(
        default=0
    )

    total_pages = models.PositiveIntegerField(
        default=0
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.UPLOADED,
        db_index=True,
    )

    error_message = models.TextField(
        blank=True,
        null=True,
    )

    created_at = models.DateTimeField(
        auto_now_add=True,
        db_index=True,
    )

    updated_at = models.DateTimeField(
        auto_now=True
    )

    class Meta:
        db_table = "documents"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["status"]),
            models.Index(fields=["created_at"]),
        ]

    def __str__(self):
        return f"{self.original_name} ({self.status})"


class DocumentBlock(models.Model):

    id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        related_name="blocks",
        db_index=True,
    )

    page_number = models.PositiveIntegerField(
        db_index=True
    )

    text = models.TextField()

    original_text = models.TextField(
        blank=True,
        default="",
        help_text="Text as originally extracted from the PDF, used to detect edited blocks",
    )

    bbox = models.JSONField(
        help_text="PDF coordinates [x0, y0, x1, y1]"
    )

    font_name = models.CharField(
        max_length=255,
        blank=True,
        default=""
    )

    font_size = models.FloatField(
        default=12
    )

    color = models.CharField(
        max_length=20,
        default="#000000"
    )

    is_bold = models.BooleanField(
        default=False
    )

    is_italic = models.BooleanField(
        default=False
    )

    has_link = models.BooleanField(
        default=False
    )

    created_at = models.DateTimeField(
        auto_now_add=True
    )

    class Meta:
        db_table = "document_blocks"
        ordering = ["page_number", "created_at"]
        indexes = [
            models.Index(fields=["document"]),
            models.Index(fields=["page_number"]),
            models.Index(fields=["document", "page_number"]),
        ]

    def __str__(self):
        return (
            f"DocumentBlock("
            f"page={self.page_number}, "
            f"document={self.document_id})"
        )