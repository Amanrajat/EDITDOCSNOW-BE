import fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import serializers

from apps.common.validation import validate_pdf_file

from .models import PageNumberJob
from .services import (
    PageNumberError,
    PageNumberService,
    validate_pages,
    validate_style,
)


def _make_pdf_file(name, page_texts, page_size=(400, 500)):
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page(width=page_size[0], height=page_size[1])
        page.insert_text(fitz.Point(20, 30), text, fontsize=12)
    data = doc.tobytes()
    doc.close()
    return SimpleUploadedFile(name, data, content_type="application/pdf")


def _find_label(pdf_bytes, page_index, label):
    """Returns the bounding rects of `label` on the given page (0-based),
    using PyMuPDF's own text search - real re-extraction, not a guess."""
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return doc[page_index].search_for(label)
    finally:
        doc.close()


def _page_size(pdf_bytes, page_index):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        rect = doc[page_index].rect
        return rect.width, rect.height
    finally:
        doc.close()


class ValidatePagesTests(TestCase):

    def test_none_means_all_pages_and_is_valid(self):
        validate_pages(None, 5)

    def test_valid_page_subset(self):
        validate_pages([1, 3], 5)

    def test_rejects_out_of_range(self):
        with self.assertRaises(PageNumberError):
            validate_pages([99], 5)

    def test_rejects_duplicate(self):
        with self.assertRaises(PageNumberError):
            validate_pages([1, 1], 5)


class ValidateStyleTests(TestCase):

    def _valid_kwargs(self, **overrides):
        kwargs = dict(
            position="bottom-center", font_size=12, font_color="#000000",
            margin=28.0, prefix="", suffix="", start_number=1,
        )
        kwargs.update(overrides)
        return kwargs

    def test_accepts_defaults(self):
        validate_style(**self._valid_kwargs())

    def test_accepts_all_six_positions(self):
        for position in [
            "top-left", "top-center", "top-right",
            "bottom-left", "bottom-center", "bottom-right",
        ]:
            validate_style(**self._valid_kwargs(position=position))

    def test_rejects_invalid_position(self):
        with self.assertRaises(PageNumberError):
            validate_style(**self._valid_kwargs(position="middle-center"))

    def test_rejects_font_size_too_small(self):
        with self.assertRaises(PageNumberError):
            validate_style(**self._valid_kwargs(font_size=2))

    def test_rejects_font_size_too_large(self):
        with self.assertRaises(PageNumberError):
            validate_style(**self._valid_kwargs(font_size=200))

    def test_accepts_3_digit_hex_color(self):
        validate_style(**self._valid_kwargs(font_color="#fff"))

    def test_rejects_malformed_color(self):
        with self.assertRaises(PageNumberError):
            validate_style(**self._valid_kwargs(font_color="red"))

    def test_rejects_negative_margin(self):
        with self.assertRaises(PageNumberError):
            validate_style(**self._valid_kwargs(margin=-5))

    def test_rejects_oversized_margin(self):
        with self.assertRaises(PageNumberError):
            validate_style(**self._valid_kwargs(margin=500))

    def test_rejects_overlong_prefix(self):
        with self.assertRaises(PageNumberError):
            validate_style(**self._valid_kwargs(prefix="x" * 41))


class PageNumberServiceTests(TestCase):

    def test_stamps_sequential_numbers_on_every_page(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        output_bytes, page_count, numbered = PageNumberService.stamp(
            pdf.read(), None, 1, "bottom-center", 12, "#000000", 20, "", "",
        )
        self.assertEqual(page_count, 3)
        self.assertEqual(numbered, [1, 2, 3])
        for i, expected in enumerate(["1", "2", "3"]):
            self.assertTrue(_find_label(output_bytes, i, expected))

    def test_start_number_offsets_first_label(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        output_bytes, _, _ = PageNumberService.stamp(
            pdf.read(), None, 5, "bottom-center", 12, "#000000", 20, "", "",
        )
        self.assertTrue(_find_label(output_bytes, 0, "5"))
        self.assertTrue(_find_label(output_bytes, 1, "6"))

    def test_numbering_is_sequential_across_a_page_subset_not_absolute(self):
        # Numbering pages 2 and 4 (skipping 1 and 3) still counts 1, 2 -
        # not "page 2" / "page 4".
        # Body text deliberately avoids bare digits (unlike "P1") so a
        # search for the stamped number "1" can't accidentally match text
        # that was already on the page.
        pdf = _make_pdf_file("doc.pdf", ["PAGE-A", "PAGE-B", "PAGE-C", "PAGE-D"])
        output_bytes, _, numbered = PageNumberService.stamp(
            pdf.read(), [2, 4], 1, "bottom-center", 12, "#000000", 20, "", "",
        )
        self.assertEqual(numbered, [2, 4])
        self.assertTrue(_find_label(output_bytes, 1, "1"))  # page index 1 = page 2
        self.assertTrue(_find_label(output_bytes, 3, "2"))  # page index 3 = page 4
        self.assertFalse(_find_label(output_bytes, 0, "1"))  # untouched page 1

    def test_prefix_and_suffix_are_applied(self):
        pdf = _make_pdf_file("doc.pdf", ["PAGE-A"])
        output_bytes, _, _ = PageNumberService.stamp(
            pdf.read(), None, 1, "bottom-center", 12, "#000000", 20, "Page ", " of 1",
        )
        self.assertTrue(_find_label(output_bytes, 0, "Page 1 of 1"))

    def test_top_left_position_places_label_near_top_left_corner(self):
        pdf = _make_pdf_file("doc.pdf", ["PAGE-A"], page_size=(400, 500))
        output_bytes, _, _ = PageNumberService.stamp(
            pdf.read(), None, 1, "top-left", 12, "#000000", 20, "", "",
        )
        rects = _find_label(output_bytes, 0, "1")
        self.assertTrue(rects)
        rect = rects[0]
        self.assertLess(rect.x0, 100)
        self.assertLess(rect.y0, 100)

    def test_bottom_right_position_places_label_near_bottom_right_corner(self):
        pdf = _make_pdf_file("doc.pdf", ["PAGE-A"], page_size=(400, 500))
        output_bytes, _, _ = PageNumberService.stamp(
            pdf.read(), None, 1, "bottom-right", 12, "#000000", 20, "", "",
        )
        width, height = _page_size(output_bytes, 0)
        rects = _find_label(output_bytes, 0, "1")
        self.assertTrue(rects)
        rect = rects[0]
        self.assertGreater(rect.x1, width - 100)
        self.assertGreater(rect.y1, height - 100)

    def test_top_center_position_is_horizontally_centered(self):
        pdf = _make_pdf_file("doc.pdf", ["PAGE-A"], page_size=(400, 500))
        output_bytes, _, _ = PageNumberService.stamp(
            pdf.read(), None, 1, "top-center", 12, "#000000", 20, "", "",
        )
        width, _ = _page_size(output_bytes, 0)
        rects = _find_label(output_bytes, 0, "1")
        self.assertTrue(rects)
        rect = rects[0]
        center = (rect.x0 + rect.x1) / 2
        self.assertAlmostEqual(center, width / 2, delta=15)

    def test_run_creates_completed_job(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        job = PageNumberService.run(
            user=None, uploaded_file=pdf, pages=None, start_number=1,
            position="bottom-center", font_size=12, font_color="#000000",
            margin=20, prefix="", suffix="",
        )
        self.assertEqual(job.status, PageNumberJob.Status.COMPLETED)
        self.assertEqual(job.page_count, 2)
        self.assertEqual(job.numbered_pages, [1, 2])
        self.assertTrue(job.output_file.name)
        job.output_file.delete(save=False)

    def test_run_deletes_job_and_raises_on_invalid_position(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        with self.assertRaises(PageNumberError):
            PageNumberService.run(
                user=None, uploaded_file=pdf, pages=None, start_number=1,
                position="middle-center", font_size=12, font_color="#000000",
                margin=20, prefix="", suffix="",
            )
        self.assertEqual(PageNumberJob.objects.count(), 0)


class PageNumberAPITests(TestCase):

    def _post(self, pdf, **overrides):
        data = {"file": pdf}
        data.update(overrides)
        return self.client.post("/api/v1/pdf/page-numbers/", data=data, format="multipart")

    def test_defaults_end_to_end(self):
        pdf = _make_pdf_file("doc.pdf", ["ORIGINAL 1", "ORIGINAL 2"])
        response = self._post(pdf)

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["data"]["page_count"], 2)
        self.assertEqual(body["data"]["numbered_pages"], [1, 2])
        self.assertEqual(body["data"]["position"], "bottom-center")

        download_url = body["data"]["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        self.assertEqual(download.status_code, 200)
        downloaded_bytes = b"".join(download.streaming_content)

        self.assertTrue(_find_label(downloaded_bytes, 0, "1"))
        self.assertTrue(_find_label(downloaded_bytes, 1, "2"))
        # Original content must survive untouched alongside the stamp.
        doc = fitz.open(stream=downloaded_bytes, filetype="pdf")
        self.assertIn("ORIGINAL 1", doc[0].get_text())
        doc.close()

        job = PageNumberJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)

    def test_custom_style_and_prefix_suffix(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        response = self._post(
            pdf, position="top-right", font_size=18, font_color="#ff0000",
            margin=15, prefix="Page ", suffix=" of 1", start_number=1,
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()

        download_url = body["data"]["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        downloaded_bytes = b"".join(download.streaming_content)
        self.assertTrue(_find_label(downloaded_bytes, 0, "Page 1 of 1"))

        job = PageNumberJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)

    def test_missing_file_is_rejected(self):
        response = self.client.post("/api/v1/pdf/page-numbers/", data={}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("file", response.json()["errors"])

    def test_invalid_position_is_rejected(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        response = self._post(pdf, position="middle-center")
        self.assertEqual(response.status_code, 400)

    def test_invalid_font_color_is_rejected(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        response = self._post(pdf, font_color="notacolor")
        self.assertEqual(response.status_code, 400)
        self.assertIn("page_numbering", response.json()["errors"])

    def test_out_of_range_page_is_rejected(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self._post(pdf, pages=[99])
        self.assertEqual(response.status_code, 400)

    def test_rejects_non_pdf_file(self):
        not_a_pdf = SimpleUploadedFile("evil.txt", b"hello", content_type="text/plain")
        response = self._post(not_a_pdf)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_rejects_oversized_file_via_shared_validator(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        with self.assertRaises(serializers.ValidationError):
            validate_pdf_file(pdf, max_size=10)


class PageNumberJobOwnershipTests(TestCase):
    """
    Access-control regression suite for the shared ownership layer, as
    exercised through Page Numbers' download endpoint. Mirrors the
    equivalent ownership test classes across every other PDF job app -
    same shared apps.common.ownership/views layer.
    """

    def _create_job(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self.client.post(
            "/api/v1/pdf/page-numbers/", data={"file": pdf}, format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["data"]

    def test_owner_can_download_with_correct_token(self):
        data = self._create_job()
        download_url = data["download_url"].replace("http://testserver", "")

        response = self.client.get(download_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

        job = PageNumberJob.objects.get(id=data["file_id"])
        job.output_file.delete(save=False)

    def test_wrong_token_is_denied(self):
        data = self._create_job()
        job = PageNumberJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/api/v1/pdf/page-numbers/{job.id}/download/?token=not-the-real-token")

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

        job.output_file.delete(save=False)

    def test_missing_token_is_denied(self):
        data = self._create_job()
        job = PageNumberJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/api/v1/pdf/page-numbers/{job.id}/download/")

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

        job.output_file.delete(save=False)

    def test_nonexistent_job_id_returns_identical_response_shape_to_wrong_token(self):
        import uuid

        response = self.client.get(f"/api/v1/pdf/page-numbers/{uuid.uuid4()}/download/?token=whatever")

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

    def test_malformed_job_id_is_handled_safely_not_a_500(self):
        response = self.client.get("/api/v1/pdf/page-numbers/not-a-valid-uuid/download/?token=whatever")
        self.assertEqual(response.status_code, 404)

    def test_raw_media_path_does_not_serve_the_output_file(self):
        data = self._create_job()
        job = PageNumberJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/media/page-numbers/output/{job.id}.pdf")

        self.assertEqual(response.status_code, 404)

        job.output_file.delete(save=False)
