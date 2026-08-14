import io
import re
import zipfile

import fitz
from django.core.files.base import ContentFile

from apps.common.ownership import generate_owner_token

from .models import SplitJob

_RANGE_TOKEN = re.compile(r"^\s*(\d+)\s*(?:-\s*(\d+)\s*)?$")


class SplitError(Exception):
    """A user-facing, 400-worthy split error (bad range/page/n vs. the
    document's actual page count) - distinct from an unexpected internal
    failure, which the view reports as a 500 PDF_PROCESSING_FAILED."""


def parse_ranges(ranges_text, total_pages):
    """
    Parse "1-5,6-10, 11" (commas and/or newlines as separators) into a list
    of 1-based inclusive (start, end) tuples, in the exact order given.

    Documented rule: ranges are NOT deduplicated, sorted, or merged. Each
    token produces exactly one output file, in input order - if ranges
    overlap or repeat (e.g. "1-3,2-5"), the overlapping pages simply appear
    in more than one output file. This is intentional: it lets a caller ask
    for the same page(s) in more than one output without a separate mode.

    Raises SplitError on: empty input, malformed tokens, a reversed range
    (start > end), or any page number outside 1..total_pages.
    """
    tokens = [t for t in re.split(r"[,\n]", ranges_text) if t.strip()]

    if not tokens:
        raise SplitError("At least one page range is required.")

    parsed = []
    for token in tokens:
        match = _RANGE_TOKEN.match(token)
        if not match:
            raise SplitError(f"'{token.strip()}' is not a valid page range.")

        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start

        if start < 1 or end < 1:
            raise SplitError(f"'{token.strip()}': page numbers must be 1 or greater.")

        if start > end:
            raise SplitError(
                f"'{token.strip()}': the start page must not be after the end page."
            )

        if end > total_pages:
            raise SplitError(
                f"'{token.strip()}': page {end} is beyond the document's "
                f"{total_pages} page(s)."
            )

        parsed.append((start, end))

    return parsed


def chunk_every_n(total_pages, n):
    """[(1, n), (n+1, 2n), ...] covering every page, last chunk possibly shorter."""
    if n < 1:
        raise SplitError("n must be at least 1.")

    chunks = []
    start = 1
    while start <= total_pages:
        end = min(start + n - 1, total_pages)
        chunks.append((start, end))
        start = end + 1

    return chunks


def _range_filename(start, end):
    return f"page_{start}.pdf" if start == end else f"pages_{start}-{end}.pdf"


class SplitPDFService:

    @staticmethod
    def _extract_range(source_doc, start, end):
        """1-based inclusive [start, end] -> standalone PDF bytes."""
        out = fitz.open()
        try:
            out.insert_pdf(source_doc, from_page=start - 1, to_page=end - 1)
            return out.tobytes(garbage=4, deflate=True)
        finally:
            out.close()

    @staticmethod
    def _extract_pages(source_doc, pages):
        """Specific 1-based page numbers, in the given order (duplicates
        allowed) -> one standalone PDF's bytes."""
        out = fitz.open()
        try:
            for page_number in pages:
                out.insert_pdf(
                    source_doc, from_page=page_number - 1, to_page=page_number - 1
                )
            return out.tobytes(garbage=4, deflate=True)
        finally:
            out.close()

    @classmethod
    def split(cls, file_bytes, mode, ranges_text=None, n=None, pages=None):
        """
        Returns a list of (filename, pdf_bytes) tuples, and the source
        document's total page count.
        """
        source_doc = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            total_pages = len(source_doc)

            if mode == SplitJob.Mode.ALL_PAGES:
                outputs = [
                    (f"page_{i + 1}.pdf", cls._extract_range(source_doc, i + 1, i + 1))
                    for i in range(total_pages)
                ]

            elif mode == SplitJob.Mode.RANGES:
                parsed_ranges = parse_ranges(ranges_text, total_pages)
                outputs = [
                    (_range_filename(start, end), cls._extract_range(source_doc, start, end))
                    for start, end in parsed_ranges
                ]

            elif mode == SplitJob.Mode.EVERY_N:
                chunks = chunk_every_n(total_pages, n)
                outputs = [
                    (_range_filename(start, end), cls._extract_range(source_doc, start, end))
                    for start, end in chunks
                ]

            elif mode == SplitJob.Mode.EXTRACT:
                for page_number in pages:
                    if page_number < 1 or page_number > total_pages:
                        raise SplitError(
                            f"page {page_number} is beyond the document's "
                            f"{total_pages} page(s)."
                        )
                outputs = [("extracted_pages.pdf", cls._extract_pages(source_doc, pages))]

            else:
                raise SplitError(f"Unknown split mode: {mode!r}")

            return outputs, total_pages
        finally:
            source_doc.close()

    @classmethod
    def run(cls, user, uploaded_file, mode, ranges_text=None, n=None, pages=None):
        """
        Full orchestration: create a SplitJob row, run the split, persist
        the result (a single PDF if there's only one output, a ZIP if
        there's more than one), and return the job.
        """
        job = SplitJob.objects.create(
            user=user,
            owner_token=generate_owner_token(),
            source_filename=uploaded_file.name,
            mode=mode,
            params={"ranges": ranges_text, "n": n, "pages": pages},
        )

        try:
            uploaded_file.seek(0)
            file_bytes = uploaded_file.read()
            outputs, total_pages = cls.split(
                file_bytes, mode, ranges_text=ranges_text, n=n, pages=pages,
            )
        except SplitError:
            job.delete()
            raise
        except Exception as exc:
            job.status = SplitJob.Status.FAILED
            job.error_message = str(exc)
            job.save(update_fields=["status", "error_message"])
            return job

        job.source_pages = total_pages
        job.output_count = len(outputs)
        job.output_filenames = [name for name, _ in outputs]

        if len(outputs) == 1:
            name, data = outputs[0]
            job.is_zip = False
            job.output_file.save(f"{job.id}.pdf", ContentFile(data), save=False)
        else:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
                for name, data in outputs:
                    archive.writestr(name, data)
            job.is_zip = True
            job.output_file.save(
                f"{job.id}.zip", ContentFile(buffer.getvalue()), save=False
            )

        job.status = SplitJob.Status.COMPLETED
        job.save(update_fields=[
            "source_pages", "output_count", "output_filenames",
            "is_zip", "output_file", "status",
        ])

        return job
