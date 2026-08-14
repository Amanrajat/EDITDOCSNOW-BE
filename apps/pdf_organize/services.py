import fitz
from django.core.files.base import ContentFile

from apps.common.ownership import generate_owner_token

from .models import OrganizeJob


class OrganizeError(Exception):
    """A user-facing, 400-worthy error: the requested page order doesn't
    match the document - distinct from an unexpected internal failure,
    which the view reports as a 500 PDF_PROCESSING_FAILED."""


def validate_order(order, page_count):
    """
    Validate `order` - a list of 1-based page numbers - is a genuine
    permutation of 1..page_count. The API is 1-based (matches what users
    see: "page 1" on screen must mean page number 1 here), not 0-based.

    Raises OrganizeError with a specific, actionable message; doesn't
    silently coerce or drop anything.
    """
    if not isinstance(order, list) or not order:
        raise OrganizeError("order must be a non-empty list of page numbers.")

    for value in order:
        if not isinstance(value, int) or isinstance(value, bool):
            raise OrganizeError(f"{value!r} is not a valid page number.")

    if len(order) != page_count:
        raise OrganizeError(
            f"order has {len(order)} page(s) but the document has "
            f"{page_count} page(s) - every page must appear exactly once."
        )

    out_of_range = sorted({v for v in order if v < 1 or v > page_count})
    if out_of_range:
        raise OrganizeError(
            f"order contains page number(s) outside 1..{page_count}: {out_of_range}."
        )

    seen = set()
    duplicates = set()
    for value in order:
        (duplicates if value in seen else seen).add(value)
    if duplicates:
        raise OrganizeError(
            f"order contains duplicate page number(s): {sorted(duplicates)}."
        )

    missing = sorted(set(range(1, page_count + 1)) - set(order))
    if missing:
        raise OrganizeError(
            f"order is missing page number(s): {missing}."
        )


class OrganizePDFService:

    @staticmethod
    def organize(file_bytes, order):
        """
        Reorder the pages of the PDF in `file_bytes` according to `order`
        (1-based page numbers, already validated against the document's
        actual page count by the caller).

        Returns (reordered_pdf_bytes, page_count).
        """
        source_doc = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            page_count = len(source_doc)
            validate_order(order, page_count)

            output = fitz.open()
            try:
                for page_number in order:
                    output.insert_pdf(
                        source_doc, from_page=page_number - 1, to_page=page_number - 1,
                    )
                output_bytes = output.tobytes(garbage=4, deflate=True)
            finally:
                output.close()

            return output_bytes, page_count
        finally:
            source_doc.close()

    @classmethod
    def run(cls, user, uploaded_file, order):
        """
        Full orchestration: create an OrganizeJob row, run the reorder,
        persist the result (or the failure reason), and return the job.
        """
        job = OrganizeJob.objects.create(
            user=user,
            owner_token=generate_owner_token(),
            original_filename=uploaded_file.name,
        )

        try:
            uploaded_file.seek(0)
            file_bytes = uploaded_file.read()
            output_bytes, page_count = cls.organize(file_bytes, order)
        except OrganizeError:
            job.delete()
            raise
        except Exception as exc:
            job.status = OrganizeJob.Status.FAILED
            job.error_message = str(exc)
            job.save(update_fields=["status", "error_message"])
            return job

        job.page_count = page_count
        job.page_order = order
        job.output_file.save(
            f"{job.id}.pdf", ContentFile(output_bytes), save=False,
        )
        job.status = OrganizeJob.Status.COMPLETED
        job.save(update_fields=["page_count", "page_order", "output_file", "status"])

        return job
