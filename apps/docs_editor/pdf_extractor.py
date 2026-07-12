import fitz


def extract_pdf_blocks(pdf_path):
    """
    Extract editable text blocks from PDF.
    Returns:
    [
        {
            "page": 0,
            "text": "...",
            "bbox": [x0, y0, x1, y1],
            "font": "Helvetica",
            "size": 12,
            "color": "#000000",
            "bold": False,
            "italic": False,
            "has_link": False,
        }
    ]
    """

    document = fitz.open(pdf_path)

    extracted_blocks = []

    try:
        for page_index in range(len(document)):

            page = document[page_index]

            links = page.get_links()

            text_dict = page.get_text("dict")

            for block in text_dict.get("blocks", []):

                if block.get("type") != 0:
                    continue

                bbox = block.get("bbox")

                text_parts = []

                font_name = ""
                font_size = 12
                # font_name = None
                # font_size = None
                # color_hex = "#000000"

                is_bold = False
                is_italic = False

                for line in block.get("lines", []):

                    for span in line.get("spans", []):

                        span_text = span.get("text", "").strip()

                        if not span_text:
                            continue

                        text_parts.append(span_text)

                        if not font_name:
                            font_name = span.get(
                                "font",
                                ""
                            )

                        if not font_size:
                            font_size = span.get(
                                "size",
                                12
                            )
                        # if font_name is None:
                        #     font_name = span.get("font", "")

                        # if font_size is None:
                        #     font_size = span.get("size", 12)

                        # color_int = span.get("color", 0)
                        # color_hex = "#{:06x}".format(color_int & 0xFFFFFF)

                        font_lower = (
                            font_name.lower()
                        )

                        is_bold = (
                            "bold" in font_lower
                        )

                        is_italic = (
                            "italic" in font_lower
                            or "oblique" in font_lower
                        )

                text = " ".join(text_parts).strip()

                if not text:
                    continue

                block_rect = fitz.Rect(bbox)

                has_link = False

                for link in links:

                    link_rect = fitz.Rect(
                        link["from"]
                    )

                    if block_rect.intersects(
                        link_rect
                    ):
                        has_link = True
                        break

                extracted_blocks.append(
                    {
                        "page": page_index,
                        "text": text,
                        "bbox": [
                            float(v)
                            for v in bbox
                        ],
                        "font": font_name,
                        "size": float(
                            font_size
                        ),
                        "color": "#000000",
                        # "color": color_hex,
                        "bold": is_bold,
                        "italic": is_italic,
                        "has_link": has_link,
                    }
                )

        return extracted_blocks

    finally:
        document.close()