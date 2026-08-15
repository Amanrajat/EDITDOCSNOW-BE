import numbers

import fitz
from django.core.files.base import ContentFile

from apps.common.ownership import generate_owner_token

from .models import CropJob

# Smallest allowed crop dimension, as a fraction of the page's own
# width/height - rejects zero-size and near-zero-size crop rectangles
# (a 0.5% sliver of a page is never a real, usable crop request).
MIN_FRACTION_SIZE = 0.02


class CropError(Exception):
    """A user-facing, 400-worthy error: a malformed crop rectangle or bad
    page numbers - distinct from an unexpected internal failure, which the
    view reports as a 500 PDF_PROCESSING_FAILED."""


def _is_number(value):
    return isinstance(value, numbers.Real) and not isinstance(value, bool)


def validate_crop_rect(x0, y0, x1, y1):
    """
    x0, y0, x1, y1: fractions (0..1) of a page's own width/height, top-left
    origin, y increasing downward - x0<x1, y0<y1, and the resulting box
    must cover at least MIN_FRACTION_SIZE of the page in both dimensions.
    """
    values = {"x0": x0, "y0": y0, "x1": x1, "y1": y1}
    for name, value in values.items():
        if not _is_number(value):
            raise CropError(f"{name} must be a number.")
        if value < 0 or value > 1:
            raise CropError(f"{name} must be between 0 and 1 (got {value!r}).")

    if x1 - x0 < MIN_FRACTION_SIZE:
        raise CropError("The crop rectangle is too narrow (or has zero/negative width).")
    if y1 - y0 < MIN_FRACTION_SIZE:
        raise CropError("The crop rectangle is too short (or has zero/negative height).")


def validate_pages(pages, page_count):
    """`pages`: list of 1-based page numbers to crop, or an empty
    list/None meaning "crop every page"."""
    if pages is None:
        return

    if not isinstance(pages, list):
        raise CropError("pages must be a list of page numbers.")

    for value in pages:
        if not isinstance(value, int) or isinstance(value, bool):
            raise CropError(f"{value!r} is not a valid page number.")

    out_of_range = sorted({v for v in pages if v < 1 or v > page_count})
    if out_of_range:
        raise CropError(
            f"pages contains page number(s) outside 1..{page_count}: {out_of_range}."
        )

    seen = set()
    duplicates = set()
    for value in pages:
        (duplicates if value in seen else seen).add(value)
    if duplicates:
        raise CropError(f"pages contains duplicate page number(s): {sorted(duplicates)}.")


def validate_crop(pages, x0, y0, x1, y1, page_count):
    """Convenience wrapper validating both - used by the service layer
    (defense in depth); the serializer calls the two halves separately so
    each error routes to the correct field."""
    validate_crop_rect(x0, y0, x1, y1)
    validate_pages(pages, page_count)


class CropPDFService:

    @staticmethod
    def crop(file_bytes, pages, x0, y0, x1, y1):
        """
        Returns (output_pdf_bytes, page_count, cropped_pages) - cropped_pages
        is the resolved 1-based list (all pages, if `pages` was empty/None).

        The same fractional rect is applied to every target page, computed
        against THAT page's own current box - so a document mixing e.g.
        A4 and Letter pages crops proportionally correct on both, not to
        one absolute rectangle that only makes sense for one page size.
        """
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            page_count = len(doc)
            validate_crop(pages, x0, y0, x1, y1, page_count)

            target_pages = pages if pages else list(range(1, page_count + 1))

            for page_number in target_pages:
                page = doc[page_number - 1]
                box = page.rect  # top-left origin, y-down page space
                new_rect = fitz.Rect(
                    box.x0 + x0 * box.width,
                    box.y0 + y0 * box.height,
                    box.x0 + x1 * box.width,
                    box.y0 + y1 * box.height,
                )
                # Set both CropBox and MediaBox to the same rect: some
                # renderers/printers ignore CropBox entirely, so a "real"
                # crop (content outside the box is genuinely gone from
                # every viewer, not just the ones that honor CropBox)
                # needs MediaBox trimmed too.
                page.set_cropbox(new_rect)
                page.set_mediabox(new_rect)

            output_bytes = doc.tobytes(garbage=4, deflate=True)
            return output_bytes, page_count, target_pages
        finally:
            doc.close()

    @classmethod
    def run(cls, user, uploaded_file, pages, x0, y0, x1, y1):
        """
        Full orchestration: create a CropJob row, run the crop, persist the
        result (or the failure reason), and return the job.
        """
        job = CropJob.objects.create(
            user=user,
            owner_token=generate_owner_token(),
            original_filename=uploaded_file.name,
            crop_rect={"x0": x0, "y0": y0, "x1": x1, "y1": y1},
        )

        try:
            uploaded_file.seek(0)
            file_bytes = uploaded_file.read()
            output_bytes, page_count, cropped_pages = cls.crop(file_bytes, pages, x0, y0, x1, y1)
        except CropError:
            job.delete()
            raise
        except Exception as exc:
            job.status = CropJob.Status.FAILED
            job.error_message = str(exc)
            job.save(update_fields=["status", "error_message"])
            return job

        job.page_count = page_count
        job.cropped_pages = cropped_pages
        job.output_file.save(
            f"{job.id}.pdf", ContentFile(output_bytes), save=False,
        )
        job.status = CropJob.Status.COMPLETED
        job.save(update_fields=["page_count", "cropped_pages", "output_file", "status"])

        return job
