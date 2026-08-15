import fitz
from django.core.files.base import ContentFile

from apps.common.ownership import generate_owner_token

from .models import RotateJob


class RotateError(Exception):
    """A user-facing, 400-worthy error: bad page numbers or a degrees value
    that isn't a real rotation - distinct from an unexpected internal
    failure, which the view reports as a 500 PDF_PROCESSING_FAILED."""


def validate_degrees(degrees):
    """
    The incremental rotation to apply - must be a non-zero multiple of 90
    (positive = clockwise, negative = counter-clockwise; both directions
    are stored the same way since PDF page rotation is always normalized
    to 0/90/180/270).
    """
    if not isinstance(degrees, int) or isinstance(degrees, bool):
        raise RotateError(f"{degrees!r} is not a valid rotation amount.")

    if degrees % 90 != 0:
        raise RotateError("degrees must be a multiple of 90.")

    if degrees % 360 == 0:
        raise RotateError("degrees must be a non-zero rotation (not a multiple of 360).")


def validate_pages(pages, page_count):
    """`pages`: list of 1-based page numbers to rotate, or an empty
    list/None meaning "rotate every page"."""
    if pages is None:
        return

    if not isinstance(pages, list):
        raise RotateError("pages must be a list of page numbers.")

    for value in pages:
        if not isinstance(value, int) or isinstance(value, bool):
            raise RotateError(f"{value!r} is not a valid page number.")

    out_of_range = sorted({v for v in pages if v < 1 or v > page_count})
    if out_of_range:
        raise RotateError(
            f"pages contains page number(s) outside 1..{page_count}: {out_of_range}."
        )

    seen = set()
    duplicates = set()
    for value in pages:
        (duplicates if value in seen else seen).add(value)
    if duplicates:
        raise RotateError(f"pages contains duplicate page number(s): {sorted(duplicates)}.")


def validate_rotation(pages, degrees, page_count):
    """Convenience wrapper validating both - used by the service layer
    (defense in depth); the serializer calls the two halves separately so
    each error routes to the correct field."""
    validate_degrees(degrees)
    validate_pages(pages, page_count)


class RotatePDFService:

    @staticmethod
    def rotate(file_bytes, pages, degrees):
        """
        Returns (output_pdf_bytes, page_count, rotated_pages) - rotated_pages
        is the resolved 1-based list (all pages, if `pages` was empty/None).
        """
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            page_count = len(doc)
            validate_rotation(pages, degrees, page_count)

            target_pages = pages if pages else list(range(1, page_count + 1))

            for page_number in target_pages:
                page = doc[page_number - 1]
                page.set_rotation((page.rotation + degrees) % 360)

            output_bytes = doc.tobytes(garbage=4, deflate=True)
            return output_bytes, page_count, target_pages
        finally:
            doc.close()

    @classmethod
    def run(cls, user, uploaded_file, pages, degrees):
        """
        Full orchestration: create a RotateJob row, run the rotation,
        persist the result (or the failure reason), and return the job.
        """
        job = RotateJob.objects.create(
            user=user,
            owner_token=generate_owner_token(),
            original_filename=uploaded_file.name,
            degrees=degrees,
        )

        try:
            uploaded_file.seek(0)
            file_bytes = uploaded_file.read()
            output_bytes, page_count, rotated_pages = cls.rotate(file_bytes, pages, degrees)
        except RotateError:
            job.delete()
            raise
        except Exception as exc:
            job.status = RotateJob.Status.FAILED
            job.error_message = str(exc)
            job.save(update_fields=["status", "error_message"])
            return job

        job.page_count = page_count
        job.rotated_pages = rotated_pages
        job.output_file.save(
            f"{job.id}.pdf", ContentFile(output_bytes), save=False,
        )
        job.status = RotateJob.Status.COMPLETED
        job.save(update_fields=["page_count", "rotated_pages", "output_file", "status"])

        return job
