import uuid

from django.conf import settings
from django.db import models

from apps.common.storage import private_job_storage


class ConversionJob(models.Model):
    """
    A single format-conversion operation (either direction: PDF -> another
    format, or another format -> PDF). One model covers every conversion
    in apps.pdf_convert.converters - they all share the same shape (one
    source file in, one result out, synchronous), so a single Django app
    with per-format converter modules is the right boundary here, not a
    separate app per conversion pair. Same ownership-token security model
    as every other job (apps.common.ownership); source file never
    persisted.
    """

    class Operation(models.TextChoices):
        PDF_TO_WORD = "pdf_to_word", "PDF to Word"
        PDF_TO_EXCEL = "pdf_to_excel", "PDF to Excel"
        PDF_TO_PPTX = "pdf_to_pptx", "PDF to PowerPoint"
        PDF_TO_JPG = "pdf_to_jpg", "PDF to JPG"
        PDF_TO_PDFA = "pdf_to_pdfa", "PDF to PDF/A"
        PDF_TO_MARKDOWN = "pdf_to_markdown", "PDF to Markdown"
        WORD_TO_PDF = "word_to_pdf", "Word to PDF"
        EXCEL_TO_PDF = "excel_to_pdf", "Excel to PDF"
        PPTX_TO_PDF = "pptx_to_pdf", "PowerPoint to PDF"
        JPG_TO_PDF = "jpg_to_pdf", "JPG to PDF"
        HTML_TO_PDF = "html_to_pdf", "HTML to PDF"

    class Status(models.TextChoices):
        PROCESSING = "processing", "Processing"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    # Output content-type/extension per operation - used by the shared
    # download view so one ConversionJob model can serve any format.
    OUTPUT_CONTENT_TYPES = {
        Operation.PDF_TO_WORD: ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
        Operation.PDF_TO_EXCEL: ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
        Operation.PDF_TO_PPTX: ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx"),
        Operation.PDF_TO_JPG: ("image/jpeg", "jpg"),  # overridden to zip when output_is_zip
        Operation.PDF_TO_PDFA: ("application/pdf", "pdf"),
        Operation.PDF_TO_MARKDOWN: ("text/markdown", "md"),
        Operation.WORD_TO_PDF: ("application/pdf", "pdf"),
        Operation.EXCEL_TO_PDF: ("application/pdf", "pdf"),
        Operation.PPTX_TO_PDF: ("application/pdf", "pdf"),
        Operation.JPG_TO_PDF: ("application/pdf", "pdf"),
        Operation.HTML_TO_PDF: ("application/pdf", "pdf"),
    }

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="conversion_jobs",
        null=True,
        blank=True,
        db_index=True,
    )

    owner_token = models.CharField(max_length=64, db_index=True, blank=True, default="")

    operation = models.CharField(max_length=20, choices=Operation.choices)
    source_filename = models.CharField(max_length=255, blank=True, default="")

    output_file = models.FileField(
        upload_to="convert/output/", storage=private_job_storage, null=True, blank=True,
    )
    output_is_zip = models.BooleanField(default=False)

    # Per-conversion facts (page_count, table_count, image_count, whether
    # a page needed the "scanned - no extractable text" image fallback,
    # etc.) - deliberately loose since every conversion reports different
    # things worth showing the user.
    metadata = models.JSONField(default=dict)

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PROCESSING,
        db_index=True,
    )
    error_message = models.TextField(blank=True, default="")

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "pdf_convert_jobs"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["status"]),
            models.Index(fields=["operation"]),
            models.Index(fields=["created_at"]),
        ]

    def get_content_type(self):
        if self.output_is_zip:
            return "application/zip"
        return self.OUTPUT_CONTENT_TYPES[self.operation][0]

    def get_filename(self):
        if self.output_is_zip:
            return f"{self.operation}_result.zip"
        ext = self.OUTPUT_CONTENT_TYPES[self.operation][1]
        return f"converted.{ext}"

    def __str__(self):
        return f"ConversionJob({self.id}, {self.operation}, {self.status})"
