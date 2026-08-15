"""
OCR: scanned/image-only PDF -> real searchable PDF, via ocrmypdf (which
itself drives Tesseract). This is not a hand-rolled "run tesseract and
hope" pipeline - ocrmypdf handles the part that's actually hard to get
right: aligning the recognized text as an invisible layer precisely over
the original page image, so the output looks identical but the text is
now selectable/searchable/copyable, and (via `skip_text`) leaving any
page that already has real text alone rather than re-OCRing or
duplicating it.
"""

import os
import shutil
import tempfile
import uuid

import fitz
import ocrmypdf
import ocrmypdf.exceptions
from django.core.files.base import ContentFile
from django.utils import timezone

from apps.common.ownership import generate_owner_token

from .models import OcrJob

# Deliberately a small, curated set matching which Tesseract language packs
# the Dockerfile actually installs - requesting a code outside this list
# fails validation with a clear error instead of a confusing Tesseract
# "traineddata not found" error deep inside ocrmypdf.
SUPPORTED_LANGUAGES = {
    "eng": "English",
    "fra": "French",
    "deu": "German",
    "spa": "Spanish",
    "hin": "Hindi",
}

MIN_TEXT_LENGTH_FOR_REAL_TEXT = 5
OCR_TIMEOUT_SECONDS = 600


class OcrError(Exception):
    """A user-facing, 400/500-worthy error: unsupported language, missing
    Tesseract, or OCR itself failing (encrypted PDF, corrupt input, etc.)."""


def validate_language(language):
    codes = [c for c in language.split("+") if c]
    if not codes:
        raise OcrError("language must not be empty.")
    invalid = sorted(set(codes) - set(SUPPORTED_LANGUAGES))
    if invalid:
        raise OcrError(
            f"Unsupported language code(s): {invalid}. Supported: {sorted(SUPPORTED_LANGUAGES)}."
        )
    return codes


def _require_tesseract():
    if shutil.which("tesseract") is None:
        raise OcrError(
            "OCR requires Tesseract ('tesseract'), which is not installed on this server."
        )


def _pages_missing_text(file_bytes):
    """1-based page numbers with no meaningfully-extractable text - the
    ones OCR is actually expected to add text to."""
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        return [
            i + 1 for i, page in enumerate(doc)
            if len(page.get_text().strip()) < MIN_TEXT_LENGTH_FOR_REAL_TEXT
        ]
    finally:
        doc.close()


def run_ocr(file_bytes, language="eng"):
    """Returns (output_pdf_bytes, metadata dict)."""
    language_codes = validate_language(language)
    _require_tesseract()

    candidate_pages = _pages_missing_text(file_bytes)

    with tempfile.TemporaryDirectory(prefix=f"ocr-{uuid.uuid4().hex}-") as tmp_dir:
        input_path = os.path.join(tmp_dir, "input.pdf")
        output_path = os.path.join(tmp_dir, "output.pdf")
        with open(input_path, "wb") as f:
            f.write(file_bytes)

        try:
            ocrmypdf.ocr(
                input_path,
                output_path,
                language=language_codes,
                skip_text=True,  # leave already-text pages untouched, don't error on them
                rotate_pages=True,  # correct rotated scans
                deskew=True,  # straighten skewed scans
                optimize=1,
                progress_bar=False,
                tesseract_timeout=OCR_TIMEOUT_SECONDS,
            )
        except ocrmypdf.exceptions.PriorOcrFoundError:
            # Every page already has real text (already OCR'd or a normal
            # text PDF) - nothing to do, not a failure.
            with open(input_path, "rb") as f:
                output_bytes = f.read()
        except ocrmypdf.exceptions.EncryptedPdfError:
            raise OcrError("This PDF is password-protected and cannot be OCR'd.")
        except Exception as exc:
            raise OcrError(f"OCR failed: {exc}")
        else:
            with open(output_path, "rb") as f:
                output_bytes = f.read()

    doc = fitz.open(stream=output_bytes, filetype="pdf")
    try:
        page_count = len(doc)
        ocr_page_count = sum(
            1
            for page_number in candidate_pages
            if page_number - 1 < page_count
            and len(doc[page_number - 1].get_text().strip()) >= MIN_TEXT_LENGTH_FOR_REAL_TEXT
        )
    finally:
        doc.close()

    metadata = {
        "page_count": page_count,
        "ocr_page_count": ocr_page_count,
        "language": language,
    }
    return output_bytes, metadata


# --- Job orchestration -----------------------------------------------
#
# OCR runs asynchronously via Celery (see tasks.py) - these functions are
# the plain-Python orchestration layer the task calls into, kept separate
# from the Celery decorator so it's trivially unit-testable without a
# broker (same split as apps.pdf_batch.services/tasks).


def create_job(user, uploaded_file, language):
    """Creates a queued OcrJob with its source file saved for the Celery
    task to pick up. Raises OcrError (job never created) if the language
    code isn't supported - callers should validate this before ever
    reaching Celery."""
    validate_language(language)

    job = OcrJob.objects.create(
        user=user,
        owner_token=generate_owner_token(),
        original_filename=uploaded_file.name,
        language=language,
        status=OcrJob.Status.QUEUED,
    )

    uploaded_file.seek(0)
    job.source_file.save(f"{job.id}.pdf", ContentFile(uploaded_file.read()), save=False)
    job.save(update_fields=["source_file"])

    return job


def process_job(job_id):
    """Runs the actual OCR for one job - called from the Celery task, but
    is plain sync code so it's directly unit-testable with
    CELERY_TASK_ALWAYS_EAGER, same as apps.pdf_batch.services.process_file."""
    try:
        job = OcrJob.objects.get(id=job_id)
    except OcrJob.DoesNotExist:
        return

    if job.status != OcrJob.Status.QUEUED:
        return  # already processed (e.g. duplicate task delivery)

    job.status = OcrJob.Status.PROCESSING
    job.save(update_fields=["status"])

    try:
        source_bytes = job.source_file.read()
        output_bytes, metadata = run_ocr(source_bytes, language=job.language)

        job.page_count = metadata["page_count"]
        job.ocr_page_count = metadata["ocr_page_count"]
        job.output_file.save(f"{job.id}.pdf", ContentFile(output_bytes), save=False)
        job.status = OcrJob.Status.COMPLETED
        job.completed_at = timezone.now()
        job.save(update_fields=[
            "page_count", "ocr_page_count", "output_file", "status", "completed_at",
        ])
    except Exception as exc:
        job.status = OcrJob.Status.FAILED
        job.error_message = str(exc)
        job.completed_at = timezone.now()
        job.save(update_fields=["status", "error_message", "completed_at"])
    finally:
        if job.source_file:
            job.source_file.delete(save=True)
