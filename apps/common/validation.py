"""
Shared file-validation helpers for PDF-processing features.

Every feature that accepts uploaded PDFs (merge, split, organize, compress,
...) needs the same baseline checks, so they live here once instead of being
copy-pasted per app. Never trust a client-supplied filename or Content-Type
header alone - this validates the actual file signature and opens it with
PyMuPDF to catch corrupted/malformed/encrypted files before any processing
is attempted.
"""

import fitz
from rest_framework import serializers

PDF_MAGIC_BYTES = b"%PDF-"

DEFAULT_MAX_FILE_SIZE = 50 * 1024 * 1024  # 50 MB
DEFAULT_MAX_PAGES = 2000


def validate_pdf_file(value, max_size=DEFAULT_MAX_FILE_SIZE, max_pages=DEFAULT_MAX_PAGES):
    """
    Validate an uploaded file is a real, parseable, size/page-bounded PDF.

    Raises rest_framework.serializers.ValidationError with a clear, per-file
    message on any failure. Returns the PDF's page count on success.
    """
    if value.size > max_size:
        raise serializers.ValidationError(
            f"'{value.name}' exceeds the maximum allowed size of "
            f"{max_size // (1024 * 1024)} MB."
        )

    if not value.name.lower().endswith(".pdf"):
        raise serializers.ValidationError(f"'{value.name}' is not a PDF file.")

    value.seek(0)
    header = value.read(len(PDF_MAGIC_BYTES))
    value.seek(0)

    if header != PDF_MAGIC_BYTES:
        raise serializers.ValidationError(
            f"'{value.name}' does not look like a valid PDF file."
        )

    try:
        data = value.read()
        value.seek(0)
        document = fitz.open(stream=data, filetype="pdf")
    except Exception:
        raise serializers.ValidationError(
            f"'{value.name}' could not be opened - it may be corrupted."
        )

    try:
        if document.is_encrypted and not document.authenticate(""):
            raise serializers.ValidationError(
                f"'{value.name}' is password-protected and cannot be processed."
            )

        page_count = len(document)
    finally:
        document.close()

    if page_count == 0:
        raise serializers.ValidationError(f"'{value.name}' has no pages.")

    if page_count > max_pages:
        raise serializers.ValidationError(
            f"'{value.name}' has {page_count} pages, exceeding the maximum "
            f"of {max_pages}."
        )

    return page_count
