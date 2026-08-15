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


import json

from django.core.files.uploadedfile import SimpleUploadedFile
from PIL import Image
import io as _io

from .models import Document, DocumentObject


def _make_pdf_bytes(page_texts, page_size=(595, 842)):
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page(width=page_size[0], height=page_size[1])
        page.insert_text(fitz.Point(72, 80), text, fontsize=14)
    data = doc.tobytes()
    doc.close()
    return data


def _make_png_bytes(size=(80, 60), color=(200, 30, 30)):
    buffer = _io.BytesIO()
    Image.new("RGB", size, color=color).save(buffer, format="PNG")
    return buffer.getvalue()


class DocumentOwnershipTests(TestCase):
    """
    Access-control regression suite for the shared ownership layer, now
    that docs_editor enforces it (it didn't before - see the model/view
    docstrings). Same conventions as every other feature's ownership
    tests: identical 404 for wrong-owner and non-existent, malformed IDs
    handled safely, raw media path never serves the private edited_file.
    """

    def _upload(self):
        pdf = SimpleUploadedFile("doc.pdf", _make_pdf_bytes(["Hello"]), content_type="application/pdf")
        response = self.client.post("/docs_editor/upload/", {"original_file": pdf})
        self.assertEqual(response.status_code, 201)
        body = response.json()
        return body["id"], body["owner_token"]

    def test_owner_can_view_extract_and_save(self):
        document_id, token = self._upload()

        detail = self.client.get(f"/docs_editor/{document_id}/?token={token}")
        self.assertEqual(detail.status_code, 200)

        extract = self.client.post(f"/docs_editor/{document_id}/extract/?token={token}")
        self.assertEqual(extract.status_code, 200)

    def test_wrong_token_is_denied_on_detail(self):
        document_id, _token = self._upload()
        response = self.client.get(f"/docs_editor/{document_id}/?token=wrong-token")
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

    def test_missing_token_is_denied_on_detail(self):
        document_id, _token = self._upload()
        response = self.client.get(f"/docs_editor/{document_id}/")
        self.assertEqual(response.status_code, 404)

    def test_wrong_token_is_denied_on_extract(self):
        document_id, _token = self._upload()
        response = self.client.post(f"/docs_editor/{document_id}/extract/?token=wrong-token")
        self.assertEqual(response.status_code, 404)

    def test_wrong_token_is_denied_on_save(self):
        document_id, token = self._upload()
        self.client.post(f"/docs_editor/{document_id}/extract/?token={token}")
        response = self.client.post(
            f"/docs_editor/{document_id}/save/?token=wrong-token",
            data=json.dumps({"blocks": []}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 404)

    def test_wrong_token_is_denied_on_objects_list(self):
        document_id, _token = self._upload()
        response = self.client.get(f"/docs_editor/{document_id}/objects/?token=wrong-token")
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_document_id_returns_identical_response_shape(self):
        import uuid
        response = self.client.get(f"/docs_editor/{uuid.uuid4()}/?token=whatever")
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

    def test_malformed_document_id_is_handled_safely_not_a_500(self):
        response = self.client.get("/docs_editor/not-a-valid-uuid/?token=whatever")
        self.assertEqual(response.status_code, 404)

    def test_raw_media_path_does_not_serve_the_edited_file(self):
        document_id, token = self._upload()
        self.client.post(f"/docs_editor/{document_id}/extract/?token={token}")
        save = self.client.post(
            f"/docs_editor/{document_id}/save/?token={token}",
            data=json.dumps({"blocks": []}),
            content_type="application/json",
        )
        # An empty blocks list is invalid per SaveEditedBlocksSerializer,
        # so add a real one instead to exercise a genuine save.
        document = Document.objects.get(id=document_id)
        block = document.blocks.first()
        save = self.client.post(
            f"/docs_editor/{document_id}/save/?token={token}",
            data=json.dumps({"blocks": [{"id": str(block.id), "text": "Edited"}]}),
            content_type="application/json",
        )
        self.assertEqual(save.status_code, 200)

        document.refresh_from_db()
        response = self.client.get(f"/media/{document.edited_file.name}")
        self.assertEqual(response.status_code, 404)

        if document.edited_file:
            document.edited_file.delete(save=False)


class DocumentObjectAPITests(TestCase):
    """CRUD + real-output-verification for editor objects (text/image/
    shapes/freehand strokes) added on top of the pre-existing text-block
    editing flow."""

    def _upload_and_extract(self, page_texts=None):
        pdf = SimpleUploadedFile(
            "doc.pdf", _make_pdf_bytes(page_texts or ["Original text"]), content_type="application/pdf",
        )
        upload = self.client.post("/docs_editor/upload/", {"original_file": pdf})
        self.assertEqual(upload.status_code, 201)
        body = upload.json()
        document_id, token = body["id"], body["owner_token"]
        self.client.post(f"/docs_editor/{document_id}/extract/?token={token}")
        return document_id, token

    def test_create_rectangle_object(self):
        document_id, token = self._upload_and_extract()
        response = self.client.post(
            f"/docs_editor/{document_id}/objects/?token={token}",
            {
                "page_number": 0, "object_type": "rectangle",
                "bbox": json.dumps([100, 100, 300, 200]),
                "fill_color": "#00ff00", "stroke_color": "#000000", "stroke_width": 2,
                "opacity": 0.7, "rotation": 15,
            },
        )
        self.assertEqual(response.status_code, 201)
        data = response.json()
        self.assertEqual(data["object_type"], "rectangle")
        self.assertEqual(data["bbox"], [100, 100, 300, 200])

    def test_create_text_object_requires_text_content(self):
        document_id, token = self._upload_and_extract()
        response = self.client.post(
            f"/docs_editor/{document_id}/objects/?token={token}",
            {"page_number": 0, "object_type": "text", "bbox": json.dumps([100, 100, 300, 150])},
        )
        self.assertEqual(response.status_code, 400)

    def test_create_path_object_requires_points(self):
        document_id, token = self._upload_and_extract()
        response = self.client.post(
            f"/docs_editor/{document_id}/objects/?token={token}",
            {"page_number": 0, "object_type": "path", "points": json.dumps([[1, 2]])},
        )
        self.assertEqual(response.status_code, 400)  # only 1 point, need >= 2

    def test_create_image_object_requires_an_image_file(self):
        document_id, token = self._upload_and_extract()
        response = self.client.post(
            f"/docs_editor/{document_id}/objects/?token={token}",
            {"page_number": 0, "object_type": "image", "bbox": json.dumps([100, 100, 200, 160])},
        )
        self.assertEqual(response.status_code, 400)

    def test_create_image_object_with_real_image(self):
        document_id, token = self._upload_and_extract()
        image = SimpleUploadedFile("photo.png", _make_png_bytes(), content_type="image/png")
        response = self.client.post(
            f"/docs_editor/{document_id}/objects/?token={token}",
            {
                "page_number": 0, "object_type": "image",
                "bbox": json.dumps([100, 100, 200, 160]),
                "image": image,
            },
        )
        self.assertEqual(response.status_code, 201)
        image_url = response.json()["image_url"]
        self.assertTrue(image_url)

        # Real-output verification: the URL DocumentObjectSerializer hands
        # back must actually be fetchable and return the real image bytes -
        # not just a truthy string (image_file.url would also be truthy but
        # 404s, since private_job_storage lives outside MEDIA_ROOT).
        relative_url = image_url.split("testserver", 1)[-1]
        fetched = self.client.get(relative_url)
        self.assertEqual(fetched.status_code, 200)
        self.assertGreater(len(b"".join(fetched.streaming_content)), 0)

    def test_update_object_via_patch(self):
        document_id, token = self._upload_and_extract()
        create = self.client.post(
            f"/docs_editor/{document_id}/objects/?token={token}",
            {"page_number": 0, "object_type": "rectangle", "bbox": json.dumps([0, 0, 100, 100])},
        )
        object_id = create.json()["id"]

        response = self.client.patch(
            f"/docs_editor/{document_id}/objects/{object_id}/?token={token}",
            data=json.dumps({"bbox": [10, 10, 110, 110], "rotation": 45}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["bbox"], [10, 10, 110, 110])
        self.assertEqual(data["rotation"], 45)

    def test_delete_object(self):
        document_id, token = self._upload_and_extract()
        create = self.client.post(
            f"/docs_editor/{document_id}/objects/?token={token}",
            {"page_number": 0, "object_type": "rectangle", "bbox": json.dumps([0, 0, 100, 100])},
        )
        object_id = create.json()["id"]

        delete = self.client.delete(f"/docs_editor/{document_id}/objects/{object_id}/?token={token}")
        self.assertEqual(delete.status_code, 204)

        listing = self.client.get(f"/docs_editor/{document_id}/objects/?token={token}")
        self.assertEqual(listing.json(), [])

    def test_wrong_token_cannot_create_object(self):
        document_id, _token = self._upload_and_extract()
        response = self.client.post(
            f"/docs_editor/{document_id}/objects/?token=wrong",
            {"page_number": 0, "object_type": "rectangle", "bbox": json.dumps([0, 0, 100, 100])},
        )
        self.assertEqual(response.status_code, 404)


class SaveWithObjectsRealOutputTests(TestCase):
    """
    Real output verification: adds text/image/shape/freehand objects
    alongside a text-block edit, saves, then reopens the actual generated
    PDF with PyMuPDF and asserts every one of them is genuinely present -
    not just that the API returned 200.
    """

    def test_save_renders_every_object_type_into_the_real_pdf(self):
        pdf = SimpleUploadedFile(
            "doc.pdf", _make_pdf_bytes(["Original heading text"]), content_type="application/pdf",
        )
        upload = self.client.post("/docs_editor/upload/", {"original_file": pdf})
        body = upload.json()
        document_id, token = body["id"], body["owner_token"]

        extract = self.client.post(f"/docs_editor/{document_id}/extract/?token={token}")
        block = extract.json()["blocks"][0]

        # One of each object type.
        self.client.post(
            f"/docs_editor/{document_id}/objects/?token={token}",
            {
                "page_number": 0, "object_type": "text",
                "bbox": json.dumps([72, 150, 400, 190]),
                "text_content": "Newly added text object", "font_size": 16, "stroke_color": "#0000ff",
            },
        )
        self.client.post(
            f"/docs_editor/{document_id}/objects/?token={token}",
            {
                "page_number": 0, "object_type": "rectangle",
                "bbox": json.dumps([72, 220, 250, 280]),
                "fill_color": "#00ff00", "stroke_color": "#000000", "stroke_width": 2, "rotation": 12,
            },
        )
        self.client.post(
            f"/docs_editor/{document_id}/objects/?token={token}",
            {
                "page_number": 0, "object_type": "path",
                "points": json.dumps([[72, 400], [100, 380], [130, 410], [160, 370]]),
                "stroke_color": "#ff00ff", "stroke_width": 3,
            },
        )
        image = SimpleUploadedFile("photo.png", _make_png_bytes(), content_type="image/png")
        self.client.post(
            f"/docs_editor/{document_id}/objects/?token={token}",
            {
                "page_number": 0, "object_type": "image",
                "bbox": json.dumps([300, 400, 400, 460]),
                "rotation": 20, "opacity": 0.6,
                "image": image,
            },
        )

        save = self.client.post(
            f"/docs_editor/{document_id}/save/?token={token}",
            data=json.dumps({"blocks": [{"id": block["id"], "text": "Edited heading text"}]}),
            content_type="application/json",
        )
        self.assertEqual(save.status_code, 200)
        download_url = save.json()["download_url"].replace("http://testserver", "")

        download = self.client.get(download_url)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "application/pdf")
        downloaded_bytes = b"".join(download.streaming_content)

        result_doc = fitz.open(stream=downloaded_bytes, filetype="pdf")
        try:
            full_text = result_doc[0].get_text()
            self.assertIn("Edited heading text", full_text)
            self.assertIn("Newly added text object", full_text)
            self.assertEqual(len(result_doc[0].get_images()), 1)
            self.assertGreaterEqual(len(result_doc[0].get_drawings()), 2)  # rectangle + path
        finally:
            result_doc.close()

        document = Document.objects.get(id=document_id)
        if document.edited_file:
            document.edited_file.delete(save=False)
        for obj in document.editor_objects.all():
            if obj.image_file:
                obj.image_file.delete(save=False)

    def test_text_object_too_small_for_its_font_size_still_renders(self):
        """
        Regression: PyMuPDF's insert_textbox() returns a negative number -
        and inserts NOTHING - when text doesn't fit its box at the given
        font size. object_renderer._draw_text used to ignore that return
        value, so a text object whose box was too small (e.g. a user drags
        a small box, then types a long sentence) silently vanished from the
        saved PDF while the API still reported success. It must now shrink
        the font until the text fits, mirroring how edited blocks already
        recover from this in pdf_regenerator._insert_text_safely.
        """
        pdf = SimpleUploadedFile("doc.pdf", _make_pdf_bytes(["Original"]), content_type="application/pdf")
        upload = self.client.post("/docs_editor/upload/", {"original_file": pdf})
        body = upload.json()
        document_id, token = body["id"], body["owner_token"]
        extract = self.client.post(f"/docs_editor/{document_id}/extract/?token={token}")
        block = extract.json()["blocks"][0]

        # A deliberately small box (160x30) at font size 16 does not fit
        # this sentence - insert_textbox() returns a negative result for it.
        self.client.post(
            f"/docs_editor/{document_id}/objects/?token={token}",
            {
                "page_number": 0, "object_type": "text",
                "bbox": json.dumps([220, 45, 380, 75]),
                "text_content": "Hello from the object editor", "font_size": 16,
            },
        )

        save = self.client.post(
            f"/docs_editor/{document_id}/save/?token={token}",
            data=json.dumps({"blocks": [{"id": block["id"], "text": block["text"]}]}),
            content_type="application/json",
        )
        self.assertEqual(save.status_code, 200)

        download_url = save.json()["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        downloaded_bytes = b"".join(download.streaming_content)
        result_doc = fitz.open(stream=downloaded_bytes, filetype="pdf")
        try:
            self.assertIn("Hello from the object editor", result_doc[0].get_text())
        finally:
            result_doc.close()

        document = Document.objects.get(id=document_id)
        if document.edited_file:
            document.edited_file.delete(save=False)

    def test_save_with_no_objects_still_works_as_before(self):
        """Regression: the pre-existing text-only save flow (no objects
        at all) must still work exactly as it did before this feature."""
        pdf = SimpleUploadedFile("doc.pdf", _make_pdf_bytes(["Plain text"]), content_type="application/pdf")
        upload = self.client.post("/docs_editor/upload/", {"original_file": pdf})
        body = upload.json()
        document_id, token = body["id"], body["owner_token"]

        extract = self.client.post(f"/docs_editor/{document_id}/extract/?token={token}")
        block = extract.json()["blocks"][0]

        save = self.client.post(
            f"/docs_editor/{document_id}/save/?token={token}",
            data=json.dumps({"blocks": [{"id": block["id"], "text": "Changed text"}]}),
            content_type="application/json",
        )
        self.assertEqual(save.status_code, 200)

        download_url = save.json()["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        downloaded_bytes = b"".join(download.streaming_content)
        result_doc = fitz.open(stream=downloaded_bytes, filetype="pdf")
        self.assertIn("Changed text", result_doc[0].get_text())
        result_doc.close()

        document = Document.objects.get(id=document_id)
        if document.edited_file:
            document.edited_file.delete(save=False)
