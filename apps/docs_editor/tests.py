import importlib
import os
import tempfile

import fitz
from django.conf import settings as dj_settings
from django.test import TestCase, override_settings
from django.urls import clear_url_caches, resolve

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


def _regenerate_heading_edit(original_text, edited_text, font="hebo", size=14):
    """
    Build a source PDF containing ONLY `original_text`, extract it exactly
    as a real upload would (bbox tight around the original text, at its
    original font/size), then regenerate it with the text changed to
    `edited_text` - same as a user editing just the text of that block.

    Returns (output_doc, original_extracted_block) so callers can assert the
    regenerated span still matches the original block's size/font/position.
    """
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "src.pdf")
        out = os.path.join(tmp, "out.pdf")

        doc = fitz.open()
        page = doc.new_page(width=400, height=150)
        page.insert_text(fitz.Point(50, 60), original_text, fontsize=size, fontname=font)
        doc.save(src)
        doc.close()

        from .pdf_extractor import extract_pdf_blocks
        extracted = extract_pdf_blocks(src)
        original = extracted[0]

        block = {
            "page": original["page"], "bbox": original["bbox"], "text": edited_text,
            "size": original["size"], "color": original["color"], "font_name": original["font"],
            "is_bold": original["bold"], "is_italic": original["italic"],
        }
        regenerate_pdf(src, out, [block])

        result = fitz.open(out)
        data = result.tobytes()
        result.close()
        return fitz.open(stream=data, filetype="pdf"), original


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


class PreservesOriginalFormattingWhenOnlyTextChangesTests(TestCase):
    """
    Editing a block's text must never change its font size, font family,
    weight/style, color, or position - only the text content. Regression
    for: editing "PROJECTS" -> "PROJECTS AMAN" rendered visibly smaller than
    the original heading, because the block's bbox (tight around the
    original, shorter text) was reused as-is and the shrink-to-fit fallback
    in _insert_text_safely kicked in the moment the longer text didn't fit
    that exact box.
    """

    def _assert_formatting_preserved(self, original_text, edited_text, font="hebo", size=14):
        doc, original = _regenerate_heading_edit(original_text, edited_text, font=font, size=size)
        try:
            page_text = doc[0].get_text().strip()
            self.assertEqual(page_text, edited_text)

            chars = _char_positions(doc)
            self.assertTrue(chars, "no glyphs were extracted from the regenerated PDF")

            expected_fontname, _ = get_font_spec({
                "font_name": original["font"],
                "is_bold": original["bold"],
                "is_italic": original["italic"],
            })

            for ch in chars:
                # font size - the ORIGINAL block's size, never recalculated
                # from the new (possibly longer or shorter) text.
                self.assertEqual(
                    ch["size"], original["size"],
                    f"font size changed from {original['size']} to {ch['size']} "
                    f"after editing {original_text!r} -> {edited_text!r}",
                )
                # font family + bold/italic style, mapped the same way
                # get_font_spec() would map the original block.
                self.assertEqual(ch["font"], expected_fontname)

            # position: left edge and baseline stay where the original text
            # was (the 1pt tolerance accounts for the anti-alias padding
            # applied to the redaction/insertion rect, not a formatting
            # change).
            first = chars[0]
            self.assertLess(abs(first["origin"][0] - original["bbox"][0]), 2.0)
            self.assertLess(abs(first["bbox"][1] - original["bbox"][1]), 2.0)

            # no glyph overlap anywhere on the (single) line.
            prev = None
            for ch in chars:
                if prev is not None and abs(ch["origin"][1] - prev["origin"][1]) < 0.1:
                    self.assertGreaterEqual(
                        round(ch["bbox"][0], 2), round(prev["bbox"][2], 2) - 0.05,
                        f"glyph {ch['c']!r} overlaps preceding glyph {prev['c']!r} "
                        f"after editing {original_text!r} -> {edited_text!r}",
                    )
                prev = ch

            return doc, chars
        finally:
            doc.close()

    def test_projects_heading_extended_with_a_word(self):
        self._assert_formatting_preserved("PROJECTS", "PROJECTS AMAN")

    def test_company_extended_with_a_word(self):
        self._assert_formatting_preserved("Company", "Company ABC")

    def test_school_name_extended_with_a_word(self):
        self._assert_formatting_preserved("School Name", "School Name XYZ")

    def test_short_text_extended_to_longer_text(self):
        self._assert_formatting_preserved("Skills", "Skills & Technologies")

    def test_long_text_shortened(self):
        self._assert_formatting_preserved(
            "Senior Backend Engineer and Team Lead", "Backend Engineer"
        )

    def test_extremely_long_replacement_falls_back_to_minimal_shrink(self):
        """
        When the edited text genuinely cannot fit even after widening the
        insertion box to the page's safe margin, a controlled fallback must
        still apply: shrink the font (never compress/overlap characters),
        keep the same font family/style, and never wipe the text.
        """
        original_text, edited_text = "Skills", "Skills: Python, Django, PostgreSQL, Docker, React, AWS, Kubernetes"
        doc, original = _regenerate_heading_edit(original_text, edited_text, font="hebo", size=14)
        try:
            page_text = doc[0].get_text().strip()
            self.assertIn(edited_text, page_text)

            chars = _char_positions(doc)
            self.assertTrue(chars)

            expected_fontname, _ = get_font_spec({
                "font_name": original["font"], "is_bold": original["bold"], "is_italic": original["italic"],
            })
            sizes = {ch["size"] for ch in chars}
            self.assertEqual(len(sizes), 1, "font size must stay consistent across the whole fallback run")
            fallback_size = sizes.pop()
            self.assertLessEqual(fallback_size, original["size"])
            self.assertGreaterEqual(fallback_size, 5.0)
            for ch in chars:
                self.assertEqual(ch["font"], expected_fontname)

            prev = None
            for ch in chars:
                if prev is not None and abs(ch["origin"][1] - prev["origin"][1]) < 0.1:
                    self.assertGreaterEqual(round(ch["bbox"][0], 2), round(prev["bbox"][2], 2) - 0.05)
                prev = ch
        finally:
            doc.close()


class RealisticTwoColumnResumeRegressionTests(TestCase):
    """
    Full-page regression using the exact two-column resume layout reported
    as producing overlapping characters: a wide left column (EXPERIENCE /
    EDUCATION, bold sans headings with an em dash, serif body text, mixed
    font sizes) next to a narrow right column (SKILLS / AWARDS) that forces
    text wrapping. Every block on the page is edited and regenerated
    together, exactly as happens when a user fills out every field of a
    resume template in one save.
    """

    def test_two_column_resume_layout_no_overlap_or_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "src.pdf")
            out = os.path.join(tmp, "out.pdf")

            doc = fitz.open()
            page = doc.new_page(width=612, height=792)

            # Left column - sans bold headings, serif body, an em dash, and
            # more than one font size.
            # Plain hyphens here, not em dashes: PyMuPDF's own Base-14
            # "simple font" encoding (used only to fabricate this test's
            # source PDF) can't represent U+2014 either, which would corrupt
            # the *source* text and defeat the point of this test. The em
            # dash is introduced below, in the user's edit - that's the
            # actual code path (our Liberation-font reinsertion) under test.
            left = [
                (60, "EXPERIENCE", 12, "hebo"),
                (84, "Company, Location - Job Title", 13, "hebo"),
                (102, "MONTH 20XX - PRESENT", 9, "helv"),
                (120, "Lorem ipsum dolor sit amet, consectetuer adipiscing elit, sed diam nonummy.", 10, "tiro"),
                (160, "EDUCATION", 12, "hebo"),
                (184, "School Name, Location - Degree", 13, "hebo"),
                (202, "MONTH 20XX - MONTH 20XX", 9, "helv"),
                (220, "Sed diam nonummy nibh euismod tincidunt ut laoreet dolore magna aliquam.", 10, "tiro"),
            ]
            for y, text, size, font in left:
                page.insert_text(fitz.Point(50, y), text, fontsize=size, fontname=font)

            # Right column - deliberately narrow, forcing our shrink-to-fit /
            # wrap logic to actually kick in.
            right = [
                (60, "SKILLS", 12, "hebo"),
                (78, "Python, Django, PostgreSQL, PyMuPDF, Docker.", 9, "helv"),
                (140, "AWARDS", 12, "hebo"),
                (158, "Lorem ipsum dolor sit amet consectetuer adipiscing elit sed diam.", 9, "helv"),
            ]
            for y, text, size, font in right:
                page.insert_text(fitz.Point(430, y), text, fontsize=size, fontname=font)

            doc.save(src)
            doc.close()

            from .pdf_extractor import extract_pdf_blocks
            extracted = extract_pdf_blocks(src)
            self.assertGreater(len(extracted), 0, "extractor found no blocks on the resume page")

            edits = {
                "Company, Location - Job Title": "WhatBytes, Location — Backend Engineer",
                "School Name, Location - Degree": "IIT Bombay, Mumbai — B.Tech CSE",
            }
            blocks = []
            for b in extracted:
                bbox_width = b["bbox"][2] - b["bbox"][0]
                blocks.append({
                    "page": b["page"],
                    # Right-column blocks are narrow by construction; keep
                    # that constraint intact instead of widening it, so this
                    # test actually exercises the narrow-column wrap path.
                    "bbox": b["bbox"] if b["bbox"][0] < 400 else [
                        b["bbox"][0], b["bbox"][1], b["bbox"][0] + max(bbox_width, 130), b["bbox"][3] + 20,
                    ],
                    "text": edits.get(b["text"], b["text"]),
                    "size": b["size"],
                    "color": b["color"],
                    "font_name": b["font"],
                    "is_bold": b["bold"],
                    "is_italic": b["italic"],
                })

            regenerate_pdf(src, out, blocks)

            result = fitz.open(out)
            try:
                page_text = result[0].get_text()
                self.assertNotIn("�", page_text)
                self.assertIn("WhatBytes, Location — Backend Engineer", page_text)
                self.assertIn("IIT Bombay, Mumbai — B.Tech CSE", page_text)
                self.assertIn("SKILLS", page_text)
                self.assertIn("AWARDS", page_text)

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
                            f"glyph {cur['c']!r} overlaps {prev['c']!r} in two-column resume layout",
                        )
            finally:
                result.close()


class MediaServingRegressionTests(TestCase):
    """
    core/urls.py must serve /media/... in every environment, including
    DEBUG=False (Render, and this project's Docker image, both run with
    DEBUG=False). django.conf.urls.static.static() looks like it does this
    but silently returns an empty urlpatterns list whenever DEBUG=False -
    that's a guard built into Django itself - which made every edited-PDF
    download 404 in production while working fine locally under DEBUG=True.
    core/urls.py now registers the `serve` view directly to bypass that
    guard; this test reloads the urlconf with DEBUG forced off and asserts
    the media route still resolves.
    """

    def test_media_url_resolves_when_debug_is_false(self):
        import core.urls as core_urls

        with override_settings(DEBUG=False):
            importlib.reload(core_urls)
            clear_url_caches()
            try:
                match = resolve(f"{dj_settings.MEDIA_URL}documents/edited/example.pdf")
                self.assertIn("serve", match.func.__name__)
            finally:
                importlib.reload(core_urls)
                clear_url_caches()
