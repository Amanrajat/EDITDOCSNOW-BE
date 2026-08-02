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


def get_font_name(block):
    """
    Map extracted font info to PyMuPDF fonts.
    """

    is_bold = block.get("is_bold", False)
    is_italic = block.get("is_italic", False)

    if is_bold and is_italic:
        return "helv-boldoblique"

    if is_bold:
        return "helv-bold"

    if is_italic:
        return "helv-oblique"

    return "helv"



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
    blocks example:

    [
        {
            "page": 0,
            "bbox": [...],
            "new_text": "Updated text",
            "size": 12,
            "color": "#000000",
            "is_bold": False,
            "is_italic": False,
        }
    ]
    """

    document = fitz.open(input_path)

    try:

        for block in blocks:

            page_number = block.get("page", 0)

            if page_number >= len(document):
                continue

            page = document[page_number]

            rect = fitz.Rect(block["bbox"])

            new_text = block.get(
                "new_text",
                block.get("text", "")
            )

            font_size = float(
                block.get("size", 12)
            )

            color = hex_to_rgb(
                block.get("color", "#000000")
            )

            font_name = get_font_name(block)

            page.add_redact_annot(
                rect,
                fill=(1, 1, 1)
            )

            page.apply_redactions()

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