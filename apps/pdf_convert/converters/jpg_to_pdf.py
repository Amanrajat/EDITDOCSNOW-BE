"""
JPG (or PNG) -> PDF. One page per image, in the given order. Real
placement math: "fit" (contain, no cropping - the whole image visible,
letterboxed if the aspect ratio doesn't match the page) or "fill" (cover -
the image is pre-cropped with Pillow to the target aspect ratio so it
fills the available area with no distortion and no letterboxing).
"""

import io

import fitz
from PIL import Image

PAGE_SIZES_PT = {
    "A4": (595.28, 841.89),
    "Letter": (612.0, 792.0),
}

MIN_MARGIN = 0
MAX_MARGIN = 150


class JpgToPdfError(Exception):
    """A user-facing, 400-worthy error: bad page size/orientation/fit
    mode/margin/quality."""


def validate_options(page_size, orientation, fit_mode, margin, quality):
    if page_size not in PAGE_SIZES_PT:
        raise JpgToPdfError(f"page_size must be one of {sorted(PAGE_SIZES_PT)} (got {page_size!r}).")
    if orientation not in ("portrait", "landscape"):
        raise JpgToPdfError(f"orientation must be 'portrait' or 'landscape' (got {orientation!r}).")
    if fit_mode not in ("fit", "fill"):
        raise JpgToPdfError(f"fit_mode must be 'fit' or 'fill' (got {fit_mode!r}).")
    if not isinstance(margin, (int, float)) or isinstance(margin, bool) or not (MIN_MARGIN <= margin <= MAX_MARGIN):
        raise JpgToPdfError(f"margin must be a number between {MIN_MARGIN} and {MAX_MARGIN}.")
    if quality is not None:
        if not isinstance(quality, int) or isinstance(quality, bool) or not (1 <= quality <= 100):
            raise JpgToPdfError("quality must be an integer between 1 and 100.")


def _page_dimensions(page_size, orientation):
    width, height = PAGE_SIZES_PT[page_size]
    if orientation == "landscape":
        width, height = height, width
    return width, height


def _prepare_image_bytes(image_bytes, avail_w, avail_h, fit_mode, quality):
    """Returns (bytes_to_embed, target_rect_w, target_rect_h) - for "fit"
    the image is untouched and the rect is the contain-scaled box; for
    "fill" the image itself is cropped to the target aspect ratio so
    inserting it into the full available rect covers it with no gaps."""
    image = Image.open(io.BytesIO(image_bytes))
    image = image.convert("RGB")
    img_w, img_h = image.size

    if fit_mode == "fit":
        scale = min(avail_w / img_w, avail_h / img_h)
        return image_bytes, img_w * scale, img_h * scale

    # fill: crop the source to the available area's aspect ratio, then it
    # can stretch to exactly fill that area with no distortion.
    target_ratio = avail_w / avail_h
    source_ratio = img_w / img_h
    if source_ratio > target_ratio:
        new_w = round(img_h * target_ratio)
        left = (img_w - new_w) // 2
        box = (left, 0, left + new_w, img_h)
    else:
        new_h = round(img_w / target_ratio)
        top = (img_h - new_h) // 2
        box = (0, top, img_w, top + new_h)
    cropped = image.crop(box)

    buffer = io.BytesIO()
    cropped.save(buffer, format="JPEG", quality=quality or 90)
    return buffer.getvalue(), avail_w, avail_h


def convert(images, page_size="A4", orientation="portrait", fit_mode="fit", margin=0, quality=None):
    """
    `images`: list of (filename, bytes) tuples, in the desired page order.
    Returns (pdf_bytes, metadata dict).
    """
    validate_options(page_size, orientation, fit_mode, margin, quality)
    if not images:
        raise JpgToPdfError("At least one image is required.")

    page_width, page_height = _page_dimensions(page_size, orientation)
    avail_w = page_width - 2 * margin
    avail_h = page_height - 2 * margin
    if avail_w <= 0 or avail_h <= 0:
        raise JpgToPdfError("margin is too large for the selected page size.")

    doc = fitz.open()
    try:
        for _name, image_bytes in images:
            page = doc.new_page(width=page_width, height=page_height)
            embed_bytes, draw_w, draw_h = _prepare_image_bytes(image_bytes, avail_w, avail_h, fit_mode, quality)

            x0 = margin + (avail_w - draw_w) / 2
            y0 = margin + (avail_h - draw_h) / 2
            rect = fitz.Rect(x0, y0, x0 + draw_w, y0 + draw_h)
            page.insert_image(rect, stream=embed_bytes)

        output_bytes = doc.tobytes(garbage=4, deflate=True)
    finally:
        doc.close()

    metadata = {
        "page_count": len(images),
        "page_size": page_size,
        "orientation": orientation,
        "fit_mode": fit_mode,
    }
    return output_bytes, metadata
