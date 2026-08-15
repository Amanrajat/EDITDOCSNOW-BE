"""
PDF -> Markdown. Extracts real document structure rather than dumping
flat text: headings (inferred from font size relative to the document's
own body-text size), paragraphs, bullet/numbered lists, bold/italic
inline emphasis, tables (via PyMuPDF's find_tables -> markdown table
syntax), and hyperlinks (via PyMuPDF's link annotations, matched to the
line whose bbox they overlap).
"""

import re
from collections import Counter

import fitz

BOLD_FLAG = 1 << 4
ITALIC_FLAG = 1 << 1
MAX_HEADING_WORDS = 15
MAX_HEADING_LEVELS = 3

_BULLET_RE = re.compile(r"^[•‣◦⁃∙*\-]\s+")
_NUMBERED_RE = re.compile(r"^\d+[.)]\s+")


def _rects_overlap_significantly(a, b, threshold=0.5):
    intersection = a & b
    if intersection.is_empty:
        return False
    a_area = a.get_area()
    if a_area == 0:
        return False
    return (intersection.get_area() / a_area) >= threshold


def _span_markdown(span):
    text = span.get("text", "")
    if not text.strip():
        return text
    flags = span.get("flags", 0)
    bold = bool(flags & BOLD_FLAG)
    italic = bool(flags & ITALIC_FLAG)
    if bold and italic:
        return f"***{text}***"
    if bold:
        return f"**{text}**"
    if italic:
        return f"*{text}*"
    return text


def _line_markdown(line):
    return "".join(_span_markdown(span) for span in line.get("spans", []))


def _line_font_size(line):
    sizes = [s.get("size", 0) for s in line.get("spans", []) if s.get("text", "").strip()]
    return max(sizes) if sizes else 0


def _table_markdown(rows):
    if not rows or not rows[0]:
        return ""
    lines = []
    header = rows[0]
    lines.append("| " + " | ".join(str(c) if c is not None else "" for c in header) + " |")
    lines.append("| " + " | ".join("---" for _ in header) + " |")
    for row in rows[1:]:
        cells = [str(c) if c is not None else "" for c in row]
        while len(cells) < len(header):
            cells.append("")
        lines.append("| " + " | ".join(cells[: len(header)]) + " |")
    return "\n".join(lines)


def _body_font_size(doc):
    # Weighted by character count, not block/span count: a document has
    # far more body-text characters than heading characters, so this
    # reflects "the size most of the text is set in" even when a short
    # document happens to have very few lines overall (where a naive
    # per-span mode could tie and arbitrarily pick a heading's size).
    char_count_by_size = Counter()
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "")
                    if text.strip():
                        char_count_by_size[round(span["size"])] += len(text)
    if not char_count_by_size:
        return 12
    return char_count_by_size.most_common(1)[0][0]


def _heading_level_map(doc, body_size):
    large_sizes = set()
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                size = _line_font_size(line)
                if size > body_size:
                    large_sizes.add(round(size))
    ranked = sorted(large_sizes, reverse=True)
    level_map = {}
    for index, size in enumerate(ranked):
        level_map[size] = min(index + 1, MAX_HEADING_LEVELS)
    return level_map


def _find_link_for_line(links, line_rect):
    for link in links:
        if not link.get("uri"):
            continue
        link_rect = fitz.Rect(link["from"])
        if _rects_overlap_significantly(line_rect, link_rect, threshold=0.3):
            return link["uri"]
    return None


def convert(file_bytes):
    """Returns (markdown_bytes, metadata dict)."""
    doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        page_count = len(doc)
        body_size = _body_font_size(doc)
        heading_levels = _heading_level_map(doc, body_size)

        output_lines = []
        table_count = 0
        heading_count = 0

        for page in doc:
            table_finder = page.find_tables()
            table_bboxes = [fitz.Rect(t.bbox) for t in table_finder.tables]

            text_dict = page.get_text("dict")
            blocks = sorted(
                (b for b in text_dict["blocks"] if b.get("type") == 0 and b.get("lines")),
                key=lambda b: (round(b["bbox"][1]), b["bbox"][0]),
            )
            links = page.get_links()

            for block in blocks:
                block_rect = fitz.Rect(block["bbox"])
                if any(_rects_overlap_significantly(block_rect, t) for t in table_bboxes):
                    continue

                lines = block.get("lines", [])
                block_words = sum(len(_line_markdown(line).split()) for line in lines)
                block_max_size = max((_line_font_size(line) for line in lines), default=0)
                is_heading = (
                    len(lines) == 1
                    and round(block_max_size) in heading_levels
                    and block_words <= MAX_HEADING_WORDS
                )

                if is_heading:
                    level = heading_levels[round(block_max_size)]
                    # Plain text, not _line_markdown: the "#" prefix already
                    # conveys emphasis - stacking **bold** on top of a
                    # heading (headings are very often set in a bold font)
                    # would just be redundant markdown syntax.
                    text = "".join(s.get("text", "") for s in lines[0].get("spans", [])).strip()
                    if text:
                        output_lines.append(f"{'#' * level} {text}")
                        output_lines.append("")
                        heading_count += 1
                    continue

                for line in lines:
                    text = _line_markdown(line).strip()
                    if not text:
                        continue
                    uri = _find_link_for_line(links, fitz.Rect(line["bbox"]))
                    if uri:
                        text = f"[{text}]({uri})"
                    if _BULLET_RE.match(text):
                        text = f"- {_BULLET_RE.sub('', text)}"
                    elif _NUMBERED_RE.match(text):
                        text = f"{_NUMBERED_RE.match(text).group().strip()} {_NUMBERED_RE.sub('', text)}"
                    output_lines.append(text)
                output_lines.append("")

            for table in table_finder.tables:
                markdown_table = _table_markdown(table.extract())
                if markdown_table:
                    output_lines.append(markdown_table)
                    output_lines.append("")
                    table_count += 1

        markdown_text = "\n".join(output_lines).strip() + "\n"
        metadata = {
            "page_count": page_count,
            "heading_count": heading_count,
            "table_count": table_count,
        }
        return markdown_text.encode("utf-8"), metadata
    finally:
        doc.close()
