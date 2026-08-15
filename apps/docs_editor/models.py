from django.db import models
from django.contrib.auth import get_user_model
import uuid

from apps.common.storage import private_job_storage

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

    owner_token = models.CharField(
        max_length=64,
        db_index=True,
        blank=True,
        default="",
        help_text=(
            "Bearer token for anonymous ownership - same shared model as "
            "every other job app (apps.common.ownership). Added after this "
            "editor's initial release, when every endpoint was open to "
            "anyone with the document's UUID; existing rows are backfilled "
            "by the migration that introduces this field."
        ),
    )

    original_file = models.FileField(
        upload_to="documents/original/"
    )

    edited_file = models.FileField(
        upload_to="documents/edited/",
        storage=private_job_storage,
        null=True,
        blank=True,
        help_text=(
            "Private storage, unlike original_file - this is the "
            "user-edited output, downloadable only through the "
            "token-gated DocumentDownloadView, matching every other "
            "feature's output_file convention."
        ),
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


class DocumentObject(models.Model):
    """
    A single editor-added object: text, an image, a shape (rectangle/
    ellipse/line/arrow), or a freehand pen/highlighter stroke. Deliberately
    a single polymorphic model (a `type` discriminator plus a superset of
    fields, most blank/unused depending on type) rather than one table per
    type - these all share the same lifecycle (add/move/resize/rotate/
    restyle/reorder/delete on one page of one document) and the same
    rendering entry point (see object_renderer.py), so one table keeps
    that lifecycle/rendering code from having to branch across multiple
    querysets.

    Separate from DocumentBlock (extracted PDF text, edited in place) by
    design - blocks have an "original" to diff against and a redact+
    reinsert regeneration path; objects here are purely additive (nothing
    to diff, they either exist or don't) and are rendered by inserting
    fresh content, never by redacting anything.
    """

    class ObjectType(models.TextChoices):
        TEXT = "text", "Text"
        IMAGE = "image", "Image"
        RECTANGLE = "rectangle", "Rectangle"
        ELLIPSE = "ellipse", "Ellipse"
        LINE = "line", "Line"
        ARROW = "arrow", "Arrow"
        PATH = "path", "Freehand path"

    TEXT_ALIGN_CHOICES = [("left", "Left"), ("center", "Center"), ("right", "Right")]
    FONT_FAMILY_CHOICES = [("sans", "Sans"), ("serif", "Serif"), ("mono", "Monospace")]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    document = models.ForeignKey(
        Document,
        on_delete=models.CASCADE,
        # NOT "objects" - that would shadow Document's own default manager
        # (Document.objects), which every query in this codebase relies on.
        related_name="editor_objects",
        db_index=True,
    )

    page_number = models.PositiveIntegerField(db_index=True, help_text="0-indexed, same convention as DocumentBlock")
    object_type = models.CharField(max_length=20, choices=ObjectType.choices, db_index=True)

    # Layer order within the page - higher draws on top. Not a global
    # z-index across the whole document, only meaningful per-page.
    z_index = models.IntegerField(default=0)

    # Geometry, PDF point space (same convention as DocumentBlock.bbox).
    # Used by every type except PATH.
    bbox = models.JSONField(default=list, blank=True, help_text="[x0, y0, x1, y1]")
    # Freehand stroke only: a flattened polyline, [[x, y], [x, y], ...].
    points = models.JSONField(default=list, blank=True)

    rotation = models.FloatField(default=0, help_text="Degrees, clockwise, about the object's own center")
    opacity = models.FloatField(default=1.0, help_text="0 (invisible) to 1 (opaque)")

    # Style - meaning varies by type (e.g. fill_color is the shape's fill
    # for rectangle/ellipse, blank/unused for line/path; stroke_color is
    # the shape/path/text color for TEXT).
    fill_color = models.CharField(max_length=9, blank=True, default="", help_text="Hex color, blank = no fill")
    stroke_color = models.CharField(max_length=9, blank=True, default="#000000")
    stroke_width = models.FloatField(default=1.0)

    # TEXT only.
    text_content = models.TextField(blank=True, default="")
    font_family = models.CharField(max_length=10, choices=FONT_FAMILY_CHOICES, default="sans")
    font_size = models.FloatField(default=14)
    is_bold = models.BooleanField(default=False)
    is_italic = models.BooleanField(default=False)
    text_align = models.CharField(max_length=10, choices=TEXT_ALIGN_CHOICES, default="left")

    # IMAGE only.
    image_file = models.FileField(
        upload_to="editor-objects/images/", storage=private_job_storage, null=True, blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "document_objects"
        ordering = ["page_number", "z_index", "created_at"]
        indexes = [
            models.Index(fields=["document"]),
            models.Index(fields=["document", "page_number"]),
        ]

    def __str__(self):
        return f"DocumentObject({self.object_type}, page={self.page_number}, document={self.document_id})"