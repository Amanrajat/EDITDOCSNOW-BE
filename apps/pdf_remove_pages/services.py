import fitz
from django.core.files.base import ContentFile

from apps.common.ownership import generate_owner_token

from .models import RemovePagesJob


class RemovePagesError(Exception):
    """A user-facing, 400-worthy error: the requested pages-to-remove list
    doesn't make sense for the document - distinct from an unexpected
    internal failure, which the view reports as a 500 PDF_PROCESSING_FAILED."""


def validate_pages_to_remove(pages, page_count):
    """
    Validate `pages` - a list of 1-based page numbers to delete - against
    the document's actual page count. 1-based, matching Organize PDF's
    convention (page 1 is what the user sees as "page 1").

    Raises RemovePagesError with a specific, actionable message. Never
    silently drops/dedupes/clamps anything.
    """
    if not isinstance(pages, list) or not pages:
        raise RemovePagesError("pages must be a non-empty list of page numbers to remove.")

    for value in pages:
        if not isinstance(value, int) or isinstance(value, bool):
            raise RemovePagesError(f"{value!r} is not a valid page number.")

    out_of_range = sorted({v for v in pages if v < 1 or v > page_count})
    if out_of_range:
        raise RemovePagesError(
            f"pages contains page number(s) outside 1..{page_count}: {out_of_range}."
        )

    seen = set()
    duplicates = set()
    for value in pages:
        (duplicates if value in seen else seen).add(value)
    if duplicates:
        raise RemovePagesError(
            f"pages contains duplicate page number(s): {sorted(duplicates)}."
        )

    if len(set(pages)) >= page_count:
        raise RemovePagesError(
            "Cannot remove all pages - the resulting PDF would be empty. "
            "Leave at least one page."
        )


class RemovePagesService:

    @staticmethod
    def remove_pages(file_bytes, pages_to_remove):
        """
        Return (output_pdf_bytes, source_page_count, output_page_count).
        Every page NOT in `pages_to_remove` is kept, in its original order
        - removal never reorders the surviving pages.
        """
        source_doc = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            page_count = len(source_doc)
            validate_pages_to_remove(pages_to_remove, page_count)

            remove_set = set(pages_to_remove)
            keep_pages = [p for p in range(1, page_count + 1) if p not in remove_set]

            output = fitz.open()
            try:
                for page_number in keep_pages:
                    output.insert_pdf(
                        source_doc, from_page=page_number - 1, to_page=page_number - 1,
                    )
                output_bytes = output.tobytes(garbage=4, deflate=True)
            finally:
                output.close()

            return output_bytes, page_count, len(keep_pages)
        finally:
            source_doc.close()

    @classmethod
    def run(cls, user, uploaded_file, pages_to_remove):
        """
        Full orchestration: create a RemovePagesJob row, run the removal,
        persist the result (or the failure reason), and return the job.
        """
        job = RemovePagesJob.objects.create(
            user=user,
            owner_token=generate_owner_token(),
            original_filename=uploaded_file.name,
        )

        try:
            uploaded_file.seek(0)
            file_bytes = uploaded_file.read()
            output_bytes, source_page_count, output_page_count = cls.remove_pages(
                file_bytes, pages_to_remove,
            )
        except RemovePagesError:
            job.delete()
            raise
        except Exception as exc:
            job.status = RemovePagesJob.Status.FAILED
            job.error_message = str(exc)
            job.save(update_fields=["status", "error_message"])
            return job

        job.source_page_count = source_page_count
        job.removed_pages = pages_to_remove
        job.output_page_count = output_page_count
        job.output_file.save(
            f"{job.id}.pdf", ContentFile(output_bytes), save=False,
        )
        job.status = RemovePagesJob.Status.COMPLETED
        job.save(update_fields=[
            "source_page_count", "removed_pages", "output_page_count",
            "output_file", "status",
        ])

        return job
