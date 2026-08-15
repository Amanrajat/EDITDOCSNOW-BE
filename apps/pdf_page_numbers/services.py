import re

import fitz
from django.core.files.base import ContentFile

from apps.common.ownership import generate_owner_token

from .models import PageNumberJob

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")

VALID_POSITIONS = {choice for choice, _ in PageNumberJob.Position.choices}

MIN_FONT_SIZE = 6
MAX_FONT_SIZE = 72
MIN_MARGIN = 0
MAX_MARGIN = 150
MAX_AFFIX_LENGTH = 40


class PageNumberError(Exception):
    """A user-facing, 400-worthy error: a malformed page-number request -
    distinct from an unexpected internal failure, which the view reports
    as a 500 PDF_PROCESSING_FAILED."""


def _hex_to_rgb01(hex_color):
    value = hex_color.lstrip("#")
    if len(value) == 3:
        value = "".join(ch * 2 for ch in value)
    r, g, b = (int(value[i : i + 2], 16) / 255 for i in (0, 2, 4))
    return (r, g, b)


def validate_pages(pages, page_count):
    """`pages`: list of 1-based page numbers to stamp, or an empty
    list/None meaning "stamp every page". Numbering proceeds sequentially
    in the order given (sorted, since numbering ascending by page order is
    the only sensible interpretation of "page range to number")."""
    if pages is None:
        return

    if not isinstance(pages, list):
        raise PageNumberError("pages must be a list of page numbers.")

    for value in pages:
        if not isinstance(value, int) or isinstance(value, bool):
            raise PageNumberError(f"{value!r} is not a valid page number.")

    out_of_range = sorted({v for v in pages if v < 1 or v > page_count})
    if out_of_range:
        raise PageNumberError(
            f"pages contains page number(s) outside 1..{page_count}: {out_of_range}."
        )

    seen = set()
    duplicates = set()
    for value in pages:
        (duplicates if value in seen else seen).add(value)
    if duplicates:
        raise PageNumberError(f"pages contains duplicate page number(s): {sorted(duplicates)}.")


def validate_style(position, font_size, font_color, margin, prefix, suffix, start_number):
    if position not in VALID_POSITIONS:
        raise PageNumberError(
            f"position must be one of {sorted(VALID_POSITIONS)} (got {position!r})."
        )

    if not isinstance(font_size, int) or isinstance(font_size, bool):
        raise PageNumberError("font_size must be an integer.")
    if not (MIN_FONT_SIZE <= font_size <= MAX_FONT_SIZE):
        raise PageNumberError(f"font_size must be between {MIN_FONT_SIZE} and {MAX_FONT_SIZE}.")

    if not isinstance(font_color, str) or not _HEX_COLOR_RE.match(font_color):
        raise PageNumberError("font_color must be a hex color like #000000.")

    if not isinstance(margin, (int, float)) or isinstance(margin, bool):
        raise PageNumberError("margin must be a number.")
    if not (MIN_MARGIN <= margin <= MAX_MARGIN):
        raise PageNumberError(f"margin must be between {MIN_MARGIN} and {MAX_MARGIN}.")

    for name, value in (("prefix", prefix), ("suffix", suffix)):
        if not isinstance(value, str):
            raise PageNumberError(f"{name} must be a string.")
        if len(value) > MAX_AFFIX_LENGTH:
            raise PageNumberError(f"{name} must be at most {MAX_AFFIX_LENGTH} characters.")

    if not isinstance(start_number, int) or isinstance(start_number, bool):
        raise PageNumberError("start_number must be an integer.")


def validate_page_numbering(pages, page_count, position, font_size, font_color, margin, prefix, suffix, start_number):
    """Convenience wrapper validating both halves - used by the service
    layer (defense in depth); the serializer calls the two halves
    separately so each error routes to the correct field."""
    validate_pages(pages, page_count)
    validate_style(position, font_size, font_color, margin, prefix, suffix, start_number)


class PageNumberService:

    @staticmethod
    def stamp(file_bytes, pages, start_number, position, font_size, font_color, margin, prefix, suffix):
        """
        Returns (output_pdf_bytes, page_count, numbered_pages) - numbered_pages
        is the resolved, sorted 1-based list (all pages, if `pages` was
        empty/None). Numbering is sequential across the target pages in
        page order, starting at `start_number` - NOT the absolute page
        number, so numbering a range that skips a cover page still counts
        1, 2, 3... from wherever it starts.
        """
        doc = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            page_count = len(doc)
            validate_page_numbering(
                pages, page_count, position, font_size, font_color, margin, prefix, suffix, start_number,
            )

            target_pages = sorted(pages) if pages else list(range(1, page_count + 1))
            rgb = _hex_to_rgb01(font_color)

            for offset, page_number in enumerate(target_pages):
                page = doc[page_number - 1]
                box = page.rect
                label = f"{prefix}{start_number + offset}{suffix}"
                text_width = fitz.get_text_length(label, fontname="helv", fontsize=font_size)

                if "left" in position:
                    x = box.x0 + margin
                elif "right" in position:
                    x = box.x1 - margin - text_width
                else:
                    x = box.x0 + (box.width - text_width) / 2

                if position.startswith("top"):
                    y = box.y0 + margin + font_size
                else:
                    y = box.y1 - margin

                page.insert_text(fitz.Point(x, y), label, fontsize=font_size, fontname="helv", color=rgb)

            output_bytes = doc.tobytes(garbage=4, deflate=True)
            return output_bytes, page_count, target_pages
        finally:
            doc.close()

    @classmethod
    def run(cls, user, uploaded_file, pages, start_number, position, font_size, font_color, margin, prefix, suffix):
        """
        Full orchestration: create a PageNumberJob row, run the stamping,
        persist the result (or the failure reason), and return the job.
        """
        job = PageNumberJob.objects.create(
            user=user,
            owner_token=generate_owner_token(),
            original_filename=uploaded_file.name,
            start_number=start_number,
            position=position,
            font_size=font_size,
            font_color=font_color,
            margin=margin,
            prefix=prefix,
            suffix=suffix,
        )

        try:
            uploaded_file.seek(0)
            file_bytes = uploaded_file.read()
            output_bytes, page_count, numbered_pages = cls.stamp(
                file_bytes, pages, start_number, position, font_size, font_color, margin, prefix, suffix,
            )
        except PageNumberError:
            job.delete()
            raise
        except Exception as exc:
            job.status = PageNumberJob.Status.FAILED
            job.error_message = str(exc)
            job.save(update_fields=["status", "error_message"])
            return job

        job.page_count = page_count
        job.numbered_pages = numbered_pages
        job.output_file.save(
            f"{job.id}.pdf", ContentFile(output_bytes), save=False,
        )
        job.status = PageNumberJob.Status.COMPLETED
        job.save(update_fields=["page_count", "numbered_pages", "output_file", "status"])

        return job
