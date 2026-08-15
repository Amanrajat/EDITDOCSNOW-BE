"""Generates real, distinguishable multi-page PDF fixtures for frontend E2E tests.

Run with the backend venv (needs PyMuPDF): .venv/bin/python scripts/make_e2e_fixture.py
Writes into ../EDITDOCSNOW-FE/e2e/fixtures/ so Playwright can upload real files
instead of fake/renamed ones.
"""

import pathlib

import fitz

OUTPUT_DIR = pathlib.Path(__file__).resolve().parent.parent.parent / "EDITDOCSNOW-FE" / "e2e" / "fixtures"


def make_labeled_pdf(filename: str, labels: list[str]) -> None:
    doc = fitz.open()
    for label in labels:
        page = doc.new_page(width=595, height=842)  # A4 portrait
        page.insert_text((72, 120), label, fontsize=36, fontname="helv")
        page.insert_text((72, 780), f"E2E fixture - {filename}", fontsize=10, fontname="helv")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT_DIR / filename)
    doc.close()


def _noisy_photo_like_png(width=1400, height=1800):
    """A real, high-entropy PNG (not a flat fill) so JPEG recompression in
    the Compress PDF pipeline has genuine content to shrink."""
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height))
    for x in range(0, width, 17):
        pix.set_rect(fitz.IRect(x, 0, x + 9, height), ((x * 7) % 255, (x * 13) % 255, (x * 31) % 255))
    for y in range(0, height, 23):
        pix.set_rect(fitz.IRect(0, y, width, y + 6), ((y * 11) % 255, (y * 5) % 255, (y * 19) % 255))
    return pix.tobytes("png")


def make_image_heavy_pdf(filename: str, page_count: int = 2) -> None:
    doc = fitz.open()
    png_bytes = _noisy_photo_like_png()
    for i in range(page_count):
        page = doc.new_page(width=612, height=792)
        page.insert_image(fitz.Rect(20, 20, 592, 500), stream=png_bytes)
        page.insert_text((40, 550), f"PHOTO PAGE {i + 1}", fontsize=18, fontname="helv")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT_DIR / filename)
    doc.close()


def make_document_with_table(filename: str) -> None:
    """A single page with a heading, a body paragraph, and a real 2x2
    table - exercises text + table extraction together (PDF->Word/Excel/
    PowerPoint/Markdown conversions all detect tables via find_tables)."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 80), "Report Heading", fontsize=22, fontname="Helvetica-Bold")
    page.insert_text((72, 120), "This is a body paragraph with real sentence content.", fontsize=12)

    page.draw_rect(fitz.Rect(72, 200, 400, 280))
    page.draw_line((236, 200), (236, 280))
    page.draw_line((72, 240), (400, 240))
    page.insert_text((90, 225), "Name")
    page.insert_text((250, 225), "Score")
    page.insert_text((90, 265), "Alice")
    page.insert_text((250, 265), "92")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    doc.save(OUTPUT_DIR / filename)
    doc.close()


if __name__ == "__main__":
    make_labeled_pdf("sample-3page.pdf", ["PAGE ONE", "PAGE TWO", "PAGE THREE"])
    make_labeled_pdf("sample-1page.pdf", ["ONLY PAGE"])
    make_image_heavy_pdf("sample-image-heavy.pdf")
    make_document_with_table("sample-with-table.pdf")
    print(f"Wrote fixtures to {OUTPUT_DIR}")
