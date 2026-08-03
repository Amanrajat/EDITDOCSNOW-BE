import shutil
from collections import defaultdict

import fitz


def hex_to_rgb(color_hex):
    """
    Convert #RRGGBB -> (r, g, b)
    values between 0 and 1
    """
    if not color_hex:
        return (0, 0, 0)

    color_hex = color_hex.strip()

    if color_hex.startswith("#"):
        color_hex = color_hex[1:]

    if len(color_hex) != 6:
        return (0, 0, 0)

    try:
        return (
            int(color_hex[0:2], 16) / 255,
            int(color_hex[2:4], 16) / 255,
            int(color_hex[4:6], 16) / 255,
        )
    except Exception:
        return (0, 0, 0)


SERIF_MARKERS = (
    "times", "serif", "georgia", "garamond", "cambria",
    "palatino", "minion", "cmr", "lmroman", "book",
)

MONOSPACE_MARKERS = (
    "courier", "mono", "consolas", "menlo", "sourcecodepro",
)


def get_font_name(block):
    """
    Best-effort map of the originally extracted font (family +
    bold/italic flags) to the closest PyMuPDF Base-14 font.

    PyMuPDF's built-in shorthand names are family-specific:
      Helvetica: helv / heit (italic) / hebo (bold) / hebi (bold-italic)
      Times:     tiro / tiit          / tibo         / tibi
      Courier:   cour / coit          / cobo         / cobi
    """

    is_bold = block.get("is_bold", False)
    is_italic = block.get("is_italic", False)

    original_font = (block.get("font_name") or "").lower()

    if any(marker in original_font for marker in MONOSPACE_MARKERS):
        family = ("cour", "coit", "cobo", "cobi")
    elif any(marker in original_font for marker in SERIF_MARKERS):
        family = ("tiro", "tiit", "tibo", "tibi")
    else:
        family = ("helv", "heit", "hebo", "hebi")

    if is_bold and is_italic:
        return family[3]

    if is_bold:
        return family[2]

    if is_italic:
        return family[1]

    return family[0]


def _insert_text_safely(
    page,
    rect,
    text,
    font_size=12,
    color=(0, 0, 0),
    font_name="helv",
):
    """
    Shrink font size until text fits.
    """

    current_size = float(font_size)

    while current_size >= 5:

        result = page.insert_textbox(
            rect,
            text,
            fontsize=current_size,
            fontname=font_name,
            color=color,
            align=0,
            overlay=True,
        )

        if result >= 0:
            return True

        current_size -= 0.5

    return False


def regenerate_pdf(
    input_path,
    output_path,
    blocks,
):
    """
    Re-inserts edited text into the original PDF.

    `blocks` should contain ONLY the blocks whose text actually
    changed - every other block is left completely untouched so the
    output is visually identical to the original except where the
    user made an edit.

    blocks example:

    [
        {
            "page": 0,
            "bbox": [...],
            "text": "Updated text",
            "size": 12,
            "color": "#000000",
            "font_name": "Times-Bold",
            "is_bold": False,
            "is_italic": False,
        }
    ]
    """

    if not blocks:
        shutil.copyfile(input_path, output_path)
        return

    document = fitz.open(input_path)

    try:
        blocks_by_page = defaultdict(list)

        for block in blocks:
            blocks_by_page[block.get("page", 0)].append(block)

        for page_number, page_blocks in blocks_by_page.items():

            if page_number < 0 or page_number >= len(document):
                continue

            page = document[page_number]
            page_bounds = page.rect

            # Pad each rect slightly so anti-aliased pixels from the
            # original glyphs at the box edges are fully removed, and
            # apply every redaction on the page in one pass.
            padded_rects = []

            for block in page_blocks:
                rect = fitz.Rect(block["bbox"])
                rect += (-1, -1, 1, 1)
                rect &= page_bounds

                padded_rects.append(rect)

                page.add_redact_annot(
                    rect,
                    fill=(1, 1, 1),
                )

            page.apply_redactions()

            for block, rect in zip(page_blocks, padded_rects):

                new_text = block.get("text", "")

                font_size = float(
                    block.get("size", 12)
                )

                color = hex_to_rgb(
                    block.get("color", "#000000")
                )

                font_name = get_font_name(block)

                _insert_text_safely(
                    page=page,
                    rect=rect,
                    text=new_text,
                    font_size=font_size,
                    color=color,
                    font_name=font_name,
                )

        document.save(
            output_path,
            garbage=4,
            deflate=True,
            clean=True,
        )

    finally:
        document.close()
