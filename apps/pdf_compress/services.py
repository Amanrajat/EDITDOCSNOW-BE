import fitz
from django.core.files.base import ContentFile

from apps.common.ownership import generate_owner_token

from .models import CompressJob

# Per-level image handling: images with a dimension above max_dimension are
# downsampled to it (aspect ratio preserved), then re-encoded as JPEG at
# jpg_quality. Text/vector content is never touched at any level - only
# raster images are recompressed, which is where PDF bloat actually lives.
LEVEL_SETTINGS = {
    CompressJob.Level.HIGH_QUALITY: {"max_dimension": 2000, "jpg_quality": 90},
    CompressJob.Level.RECOMMENDED: {"max_dimension": 1600, "jpg_quality": 75},
    CompressJob.Level.HIGH_COMPRESSION: {"max_dimension": 1200, "jpg_quality": 50},
    CompressJob.Level.MAXIMUM_COMPRESSION: {"max_dimension": 800, "jpg_quality": 30},
}

VALID_LEVELS = {choice for choice, _ in CompressJob.Level.choices}


class CompressError(Exception):
    """A user-facing, 400-worthy error: an invalid compression level -
    distinct from an unexpected internal failure, which the view reports
    as a 500 PDF_PROCESSING_FAILED."""


def validate_level(level):
    if level not in VALID_LEVELS:
        raise CompressError(f"level must be one of {sorted(VALID_LEVELS)} (got {level!r}).")


def _compress_image(doc, page, xref, max_dimension, jpg_quality):
    """Downsamples + recompresses one embedded image in place, but only if
    doing so actually makes it smaller - never makes an image worse."""
    if doc.xref_get_key(xref, "ImageMask")[1] == "true":
        return  # stencil masks aren't real images - recompressing as JPEG would corrupt them

    pixmap = fitz.Pixmap(doc, xref)
    try:
        if pixmap.alpha:
            pixmap = fitz.Pixmap(pixmap, 0)  # JPEG has no alpha channel
        if pixmap.colorspace is None:
            return
        if pixmap.colorspace.n >= 4:  # CMYK or other multi-channel - normalize to RGB
            pixmap = fitz.Pixmap(fitz.csRGB, pixmap)

        width, height = pixmap.width, pixmap.height
        largest_side = max(width, height)
        if largest_side > max_dimension:
            scale = max_dimension / largest_side
            new_width = max(1, round(width * scale))
            new_height = max(1, round(height * scale))
            pixmap = fitz.Pixmap(pixmap, new_width, new_height)

        new_bytes = pixmap.tobytes("jpg", jpg_quality=jpg_quality)
        original_stream = doc.xref_stream_raw(xref) or b""
        if len(new_bytes) < len(original_stream):
            page.replace_image(xref, stream=new_bytes)
    finally:
        pixmap = None


class CompressPDFService:

    @staticmethod
    def compress(file_bytes, level):
        """
        Returns (output_bytes, page_count, original_size, compressed_size).
        Falls back to the original bytes if processing didn't actually
        shrink the file (e.g. an already-optimized, image-free PDF) - a
        "compressed" file must never come back larger than the original.
        """
        validate_level(level)
        settings = LEVEL_SETTINGS[level]
        original_size = len(file_bytes)

        doc = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            page_count = len(doc)

            processed_xrefs = set()
            for page in doc:
                for image in page.get_images(full=True):
                    xref = image[0]
                    if xref in processed_xrefs:
                        continue
                    processed_xrefs.add(xref)
                    try:
                        _compress_image(doc, page, xref, settings["max_dimension"], settings["jpg_quality"])
                    except Exception:
                        # Best-effort: one malformed/unsupported image must
                        # not fail the whole job - leave it untouched.
                        continue

            doc.set_metadata({})
            output_bytes = doc.tobytes(garbage=4, deflate=True, clean=True)
        finally:
            doc.close()

        compressed_size = len(output_bytes)
        if compressed_size >= original_size:
            return file_bytes, page_count, original_size, original_size

        return output_bytes, page_count, original_size, compressed_size

    @classmethod
    def run(cls, user, uploaded_file, level):
        """
        Full orchestration: create a CompressJob row, run the compression,
        persist the result (or the failure reason), and return the job.
        """
        job = CompressJob.objects.create(
            user=user,
            owner_token=generate_owner_token(),
            original_filename=uploaded_file.name,
            level=level,
        )

        try:
            uploaded_file.seek(0)
            file_bytes = uploaded_file.read()
            output_bytes, page_count, original_size, compressed_size = cls.compress(file_bytes, level)
        except CompressError:
            job.delete()
            raise
        except Exception as exc:
            job.status = CompressJob.Status.FAILED
            job.error_message = str(exc)
            job.save(update_fields=["status", "error_message"])
            return job

        job.page_count = page_count
        job.original_size = original_size
        job.compressed_size = compressed_size
        job.output_file.save(
            f"{job.id}.pdf", ContentFile(output_bytes), save=False,
        )
        job.status = CompressJob.Status.COMPLETED
        job.save(update_fields=["page_count", "original_size", "compressed_size", "output_file", "status"])

        return job
