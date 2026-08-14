import os
import tempfile

import fitz
from django.test import TestCase

from .pdf_regenerator import regenerate_pdf, get_font_spec


def _build_source_pdf(path, runs, fontsize=13, x=50, y=60, page_size=(300, 200)):
    """
    Build a one-page PDF containing `runs` — a list of (text, base14_fontname)
    tuples drawn left-to-right on a single baseline — and return the bbox
    each run occupies, mimicking what a real PDF's original (non-Liberation)
    font would look like before our substitution.
    """
    doc = fitz.open()
    page = doc.new_page(width=page_size[0], height=page_size[1])
    cx = x
    for text, fname in runs:
        page.insert_text(fitz.Point(cx, y), text, fontsize=fontsize, fontname=fname)
        cx += fitz.Font(fname).text_length(text, fontsize=fontsize)
    doc.save(path)
    doc.close()
    return x, y, cx, fontsize


def _regenerate_single_block(text, font_name="Helvetica-Bold", is_bold=True,
                              is_italic=False, fontsize=16, bbox_width=None,
                              bbox_height=24, color="#000000"):
    """
    Build a minimal source PDF (unrelated placeholder ink), then regenerate a
    single block of `text` into it using our production regenerate_pdf(),
    exactly like a real save/regenerate would for one edited block.
    Returns the fitz.Document of the regenerated PDF (caller must close it).
    """
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src.pdf")
        out = os.path.join(tmp, "out.pdf")

        doc = fitz.open()
        page = doc.new_page(width=400, height=200)
        page.insert_text(fitz.Point(50, 60), "placeholder", fontsize=fontsize, fontname="helv")
        doc.save(src)
        doc.close()

        if bbox_width is None:
            # Generous width so we're testing font rendering, not shrink-to-fit.
            bbox_width = fitz.Font("helv").text_length(text, fontsize=fontsize) * 1.8 + 40

        bbox = [50, 40, 50 + bbox_width, 40 + bbox_height]
        block = {
            "page": 0,
            "bbox": bbox,
            "text": text,
            "size": fontsize,
            "color": color,
            "font_name": font_name,
            "is_bold": is_bold,
            "is_italic": is_italic,
        }
        regenerate_pdf(src, out, [block])

        result = fitz.open(out)
        # Force fitz to read the saved bytes back fresh (avoid any in-memory
        # state leaking from the writer), matching how the real download flow
        # re-opens the saved file from disk.
        data = result.tobytes()
        result.close()
        return fitz.open(stream=data, filetype="pdf")


def _char_positions(doc, page_index=0):
    """Flatten every glyph's (char, origin_x, bbox, font) across a page, in order."""
    raw = doc[page_index].get_text("rawdict")
    chars = []
    for block in raw["blocks"]:
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                for ch in span.get("chars", []):
                    chars.append({
                        "c": ch["c"],
                        "origin": ch["origin"],
                        "bbox": ch["bbox"],
                        "font": span["font"],
                        "size": span["size"],
                    })
    return chars


class RegeneratePdfTextIntegrityTests(TestCase):
    """
    Regression tests for the font-substitution pipeline in pdf_regenerator.py.

    These assert two independent things for every case:
      1. The extracted text round-trips exactly (no "?" corruption, no
         dropped/altered characters).
      2. No two consecutive glyphs on the same line overlap horizontally —
         i.e. glyph N+1's left edge is never to the left of glyph N's right
         edge. This is the concrete, automatable proxy for "characters are
         not visually touching/overlapping".
    """

    def _assert_no_overlap_and_text(self, text, expected_text=None, **kwargs):
        doc = _regenerate_single_block(text, **kwargs)
        try:
            page_text = doc[0].get_text().replace("\n", " ").strip()
            self.assertEqual(page_text, (expected_text or text))

            chars = _char_positions(doc)
            self.assertTrue(chars, "no glyphs were extracted from the regenerated PDF")

            prev = None
            for ch in chars:
                if prev is not None and abs(ch["origin"][1] - prev["origin"][1]) < 0.1:
                    # same baseline -> same line; enforce no horizontal overlap
                    self.assertGreaterEqual(
                        round(ch["bbox"][0], 2), round(prev["bbox"][2], 2) - 0.05,
                        f"glyph {ch['c']!r} overlaps preceding glyph {prev['c']!r} "
                        f"in text {text!r} (prev right edge={prev['bbox'][2]:.2f}, "
                        f"this left edge={ch['bbox'][0]:.2f})",
                    )
                prev = ch
            return doc, chars
        finally:
            doc.close()

    # --- exact words called out in the bug report ---

    def test_company_bold(self):
        self._assert_no_overlap_and_text("Company", is_bold=True, is_italic=False)

    def test_school_name_bold(self):
        self._assert_no_overlap_and_text("School Name", is_bold=True, is_italic=False)

    def test_aman_srivastava(self):
        self._assert_no_overlap_and_text("Aman Srivastava", is_bold=True, is_italic=False)

    def test_whatbytes_heading_with_em_dash(self):
        self._assert_no_overlap_and_text(
            "WhatBytes, Location — Job Title", is_bold=True, is_italic=False
        )

    # --- general ASCII / Unicode coverage ---

    def test_plain_ascii_sentence(self):
        self._assert_no_overlap_and_text(
            "The quick brown fox jumps over the lazy dog.",
            is_bold=False, is_italic=False,
        )

    def test_unicode_punctuation(self):
        self._assert_no_overlap_and_text(
            "em—dash en–dash bullet•point",
            is_bold=False, is_italic=False,
        )

    def test_curly_quotes(self):
        self._assert_no_overlap_and_text(
            "“quoted” and ‘single’",
            is_bold=False, is_italic=False,
        )

    def test_accented_characters(self):
        self._assert_no_overlap_and_text(
            "café naïve résumé",
            is_bold=False, is_italic=False,
        )

    def test_literal_question_mark_is_preserved(self):
        """A real '?' typed by the user must remain a real '?', not be
        touched by the Unicode-corruption fix or anything else."""
        doc = _regenerate_single_block("Is this correct?", is_bold=False, is_italic=False)
        try:
            self.assertIn("?", doc[0].get_text())
            self.assertNotIn("�", doc[0].get_text())
        finally:
            doc.close()

    # --- style / family coverage ---

    def test_bold(self):
        doc, _ = self._assert_no_overlap_and_text("Bold text sample", is_bold=True, is_italic=False)

    def test_italic(self):
        self._assert_no_overlap_and_text("Italic text sample", is_bold=False, is_italic=True)

    def test_bold_italic(self):
        self._assert_no_overlap_and_text("Bold italic sample", is_bold=True, is_italic=True)

    def test_serif_font_family(self):
        doc = _regenerate_single_block(
            "Serif family sample", font_name="Times-Bold", is_bold=True, is_italic=False
        )
        try:
            fontname, fontfile = get_font_spec(
                {"font_name": "Times-Bold", "is_bold": True, "is_italic": False}
            )
            self.assertIn("Serif", fontname)
            self.assertTrue(os.path.exists(fontfile))
        finally:
            doc.close()

    def test_sans_font_family(self):
        fontname, fontfile = get_font_spec(
            {"font_name": "Arial-Bold", "is_bold": True, "is_italic": False}
        )
        self.assertIn("Sans", fontname)
        self.assertTrue(os.path.exists(fontfile))

    def test_monospace_font_family(self):
        fontname, fontfile = get_font_spec(
            {"font_name": "CourierNewPSMT", "is_bold": False, "is_italic": False}
        )
        self.assertIn("Mono", fontname)
        self.assertTrue(os.path.exists(fontfile))

    # --- layout coverage ---

    def test_multiline_text_wraps_without_vertical_overlap(self):
        doc = _regenerate_single_block(
            "This is a longer line of text that must wrap across multiple lines "
            "inside a narrow column without the lines colliding.",
            is_bold=False, is_italic=False,
            bbox_width=140, bbox_height=90,
        )
        try:
            chars = _char_positions(doc)
            baselines = sorted(set(round(c["origin"][1], 1) for c in chars))
            self.assertGreater(len(baselines), 1, "expected text to wrap onto multiple lines")
            for a, b in zip(baselines, baselines[1:]):
                self.assertGreater(b, a, "line baselines must be strictly increasing")
        finally:
            doc.close()

    def test_right_column_narrow_text(self):
        """Narrow right-column text (e.g. a skills list) must not overlap."""
        self._assert_no_overlap_and_text(
            "Lorem ipsum dolor sit amet.", is_bold=False, is_italic=False,
            bbox_width=110, bbox_height=60,
        )

    def test_realistic_multi_block_page_no_overlap(self):
        """
        Full-page regression: several differently-styled blocks (bold
        heading, regular date line, regular body text) redacted and
        reinserted together on one page, as happens on every real save.
        """
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.pdf")
            out = os.path.join(tmp, "out.pdf")

            doc = fitz.open()
            page = doc.new_page()
            page.insert_text(fitz.Point(50, 60), "Company, Location - Job Title", fontsize=13, fontname="hebo")
            page.insert_text(fitz.Point(50, 78), "MONTH 20XX - PRESENT", fontsize=9, fontname="helv")
            page.insert_text(fitz.Point(50, 96), "Lorem ipsum dolor sit amet, consectetuer.", fontsize=10, fontname="helv")
            page.insert_text(fitz.Point(50, 130), "School Name, Location - Degree", fontsize=13, fontname="hebo")
            doc.save(src)
            doc.close()

            from .pdf_extractor import extract_pdf_blocks
            extracted = extract_pdf_blocks(src)
            blocks = [{
                "page": b["page"], "bbox": b["bbox"], "text": b["text"],
                "size": b["size"], "color": b["color"], "font_name": b["font"],
                "is_bold": b["bold"], "is_italic": b["italic"],
            } for b in extracted]

            regenerate_pdf(src, out, blocks)

            result = fitz.open(out)
            try:
                chars = _char_positions(result)
                by_line = {}
                for ch in chars:
                    key = round(ch["origin"][1], 1)
                    by_line.setdefault(key, []).append(ch)
                for _, line_chars in by_line.items():
                    line_chars.sort(key=lambda c: c["origin"][0])
                    for prev, cur in zip(line_chars, line_chars[1:]):
                        self.assertGreaterEqual(
                            round(cur["bbox"][0], 2), round(prev["bbox"][2], 2) - 0.05,
                            f"glyph {cur['c']!r} overlaps {prev['c']!r} in regenerated page",
                        )
            finally:
                result.close()
