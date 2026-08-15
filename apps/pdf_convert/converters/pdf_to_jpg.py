"""
PDF -> JPG. Real page rasterization via PyMuPDF (page.get_pixmap at the
requested DPI, re-encoded as JPEG at the requested quality) - not a
placeholder/blank image. A single requested page returns one .jpg; more
than one is zipped, mirroring Split PDF's is_zip convention.
"""

import io
import zipfile

import fitz

DEFAULT_DPI = 150
MIN_DPI = 72
MAX_DPI = 600
DEFAULT_QUALITY = 90
MIN_QUALITY = 1
MAX_QUALITY = 100


class PdfToJpgError(Exception):
    """A user-facing, 400-worthy error: bad page numbers/dpi/quality."""


def validate_pages(pages, page_count):
    if pages is None:
        return
    if not isinstance(pages, list):
        raise PdfToJpgError("pages must be a list of page numbers.")
    for value in pages:
        if not isinstance(value, int) or isinstance(value, bool):
            raise PdfToJpgError(f"{value!r} is not a valid page number.")
    out_of_range = sorted({v for v in pages if v < 1 or v > page_count})
    if out_of_range:
        raise PdfToJpgError(f"pages contains page number(s) outside 1..{page_count}: {out_of_range}.")


def validate_dpi(dpi):
    if not isinstance(dpi, int) or isinstance(dpi, bool):
        raise PdfToJpgError("dpi must be an integer.")
    if not (MIN_DPI <= dpi <= MAX_DPI):
        raise PdfToJpgError(f"dpi must be between {MIN_DPI} and {MAX_DPI}.")


def validate_quality(quality):
    if not isinstance(quality, int) or isinstance(quality, bool):
        raise PdfToJpgError("quality must be an integer.")
    if not (MIN_QUALITY <= quality <= MAX_QUALITY):
        raise PdfToJpgError(f"quality must be between {MIN_QUALITY} and {MAX_QUALITY}.")


def convert(file_bytes, pages=None, dpi=DEFAULT_DPI, quality=DEFAULT_QUALITY):
    """Returns (output_bytes, metadata dict) - metadata includes the
    "_output_ext"/"_is_zip" keys run_conversion() looks for."""
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        page_count = len(doc)
        validate_pages(pages, page_count)
        validate_dpi(dpi)
        validate_quality(quality)

        target_pages = pages if pages else list(range(1, page_count + 1))

        images = []
        for page_number in target_pages:
            page = doc[page_number - 1]
            pix = page.get_pixmap(dpi=dpi)
            jpg_bytes = pix.tobytes("jpg", jpg_quality=quality)
            images.append((f"page_{page_number}.jpg", jpg_bytes))

        metadata = {
            "page_count": page_count,
            "converted_pages": target_pages,
            "dpi": dpi,
            "quality": quality,
        }

        if len(images) == 1:
            metadata["_output_ext"] = "jpg"
            metadata["_is_zip"] = False
            return images[0][1], metadata

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in images:
                zf.writestr(name, data)
        metadata["_output_ext"] = "zip"
        metadata["_is_zip"] = True
        return buffer.getvalue(), metadata
    finally:
        doc.close()
