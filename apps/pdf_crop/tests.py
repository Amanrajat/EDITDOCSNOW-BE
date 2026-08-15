import fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import serializers

from apps.common.validation import validate_pdf_file

from .models import CropJob
from .services import CropError, CropPDFService, validate_crop_rect, validate_pages


def _make_pdf_file(name, page_texts, page_size=(300, 200)):
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page(width=page_size[0], height=page_size[1])
        page.insert_text(fitz.Point(50, 100), text, fontsize=14)
    data = doc.tobytes()
    doc.close()
    return SimpleUploadedFile(name, data, content_type="application/pdf")


def _make_multi_size_pdf_file(name, specs):
    """`specs`: list of (text, width, height) - lets tests build a document
    that mixes page sizes, e.g. A4 and Letter, in one file."""
    doc = fitz.open()
    for text, width, height in specs:
        page = doc.new_page(width=width, height=height)
        page.insert_text(fitz.Point(20, 30), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return SimpleUploadedFile(name, data, content_type="application/pdf")


def _page_sizes(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [(round(page.rect.width, 1), round(page.rect.height, 1)) for page in doc]
    finally:
        doc.close()


def _page_texts(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [page.get_text().strip() for page in doc]
    finally:
        doc.close()


class ValidateCropRectTests(TestCase):

    def test_accepts_full_page(self):
        validate_crop_rect(0, 0, 1, 1)

    def test_accepts_inset_rect(self):
        validate_crop_rect(0.1, 0.1, 0.9, 0.9)

    def test_rejects_zero_width(self):
        with self.assertRaises(CropError):
            validate_crop_rect(0.5, 0.1, 0.5, 0.9)

    def test_rejects_zero_height(self):
        with self.assertRaises(CropError):
            validate_crop_rect(0.1, 0.5, 0.9, 0.5)

    def test_rejects_negative_coordinate(self):
        with self.assertRaises(CropError):
            validate_crop_rect(-0.1, 0, 1, 1)

    def test_rejects_coordinate_above_one(self):
        with self.assertRaises(CropError):
            validate_crop_rect(0, 0, 1.5, 1)

    def test_rejects_inverted_rect(self):
        with self.assertRaises(CropError):
            validate_crop_rect(0.8, 0.1, 0.2, 0.9)

    def test_rejects_below_minimum_size(self):
        with self.assertRaises(CropError):
            validate_crop_rect(0.5, 0.5, 0.505, 0.505)

    def test_rejects_non_numeric(self):
        with self.assertRaises(CropError):
            validate_crop_rect("0", 0, 1, 1)

    def test_rejects_bool(self):
        with self.assertRaises(CropError):
            validate_crop_rect(True, 0, 1, 1)


class ValidatePagesTests(TestCase):

    def test_none_means_all_pages_and_is_valid(self):
        validate_pages(None, 5)

    def test_valid_page_subset(self):
        validate_pages([1, 3], 5)

    def test_rejects_out_of_range(self):
        with self.assertRaises(CropError):
            validate_pages([99], 5)

    def test_rejects_duplicate(self):
        with self.assertRaises(CropError):
            validate_pages([1, 1], 5)

    def test_rejects_non_integer(self):
        with self.assertRaises(CropError):
            validate_pages(["1"], 5)


class CropPDFServiceTests(TestCase):

    def test_crop_single_page_to_half_size(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"], page_size=(300, 200))
        output_bytes, page_count, cropped = CropPDFService.crop(pdf.read(), [1], 0, 0, 0.5, 0.5)

        self.assertEqual(page_count, 2)
        self.assertEqual(cropped, [1])
        sizes = _page_sizes(output_bytes)
        self.assertEqual(sizes[0], (150.0, 100.0))
        self.assertEqual(sizes[1], (300.0, 200.0))  # untouched page keeps its original size

    def test_crop_all_pages(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"], page_size=(400, 400))
        output_bytes, _, cropped = CropPDFService.crop(pdf.read(), None, 0.25, 0.25, 0.75, 0.75)

        self.assertEqual(cropped, [1, 2, 3])
        sizes = _page_sizes(output_bytes)
        self.assertEqual(sizes, [(200.0, 200.0)] * 3)

    def test_crop_preserves_text_content(self):
        pdf = _make_pdf_file("doc.pdf", ["Keep me"], page_size=(300, 200))
        # Text sits near (50, 100) - crop to a rect that still contains it.
        output_bytes, _, _ = CropPDFService.crop(pdf.read(), [1], 0, 0, 1, 1)
        self.assertEqual(_page_texts(output_bytes), ["Keep me"])

    def test_crop_applies_proportionally_across_mixed_page_sizes(self):
        pdf = _make_multi_size_pdf_file(
            "mixed.pdf", [("A4-ish", 595, 842), ("Letter-ish", 612, 792)]
        )
        output_bytes, page_count, cropped = CropPDFService.crop(pdf.read(), None, 0, 0, 0.5, 0.5)

        self.assertEqual(page_count, 2)
        self.assertEqual(cropped, [1, 2])
        sizes = _page_sizes(output_bytes)
        self.assertEqual(sizes[0], (297.5, 421.0))
        self.assertEqual(sizes[1], (306.0, 396.0))

    def test_run_creates_completed_job(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"], page_size=(300, 200))
        job = CropPDFService.run(user=None, uploaded_file=pdf, pages=None, x0=0, y0=0, x1=0.5, y1=0.5)

        self.assertEqual(job.status, CropJob.Status.COMPLETED)
        self.assertEqual(job.page_count, 1)
        self.assertEqual(job.cropped_pages, [1])
        self.assertEqual(job.crop_rect, {"x0": 0, "y0": 0, "x1": 0.5, "y1": 0.5})
        self.assertTrue(job.output_file.name)

        job.output_file.delete(save=False)

    def test_run_deletes_job_and_raises_on_invalid_rect(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        with self.assertRaises(CropError):
            CropPDFService.run(user=None, uploaded_file=pdf, pages=None, x0=0.9, y0=0, x1=0.1, y1=1)
        self.assertEqual(CropJob.objects.count(), 0)


class CropPDFAPITests(TestCase):

    def _post(self, pdf, x0=0, y0=0, x1=1, y1=1, pages=None):
        data = {"file": pdf, "x0": x0, "y0": y0, "x1": x1, "y1": y1}
        if pages is not None:
            data["pages"] = pages
        return self.client.post("/api/v1/pdf/crop/", data=data, format="multipart")

    def test_crop_all_pages_end_to_end(self):
        pdf = _make_pdf_file("doc.pdf", ["ORIGINAL PAGE 1", "ORIGINAL PAGE 2"], page_size=(400, 400))
        response = self._post(pdf, 0, 0, 0.5, 0.5)

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["message"], "PDF cropped successfully")
        self.assertEqual(body["data"]["page_count"], 2)
        self.assertEqual(body["data"]["cropped_pages"], [1, 2])

        download_url = body["data"]["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "application/pdf")

        downloaded_bytes = b"".join(download.streaming_content)
        self.assertEqual(_page_sizes(downloaded_bytes), [(200.0, 200.0), (200.0, 200.0)])

        job = CropJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)

    def test_crop_specific_page(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"], page_size=(300, 200))
        response = self._post(pdf, 0, 0, 0.5, 1, pages=[2])

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["data"]["cropped_pages"], [2])

        download_url = body["data"]["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        downloaded_bytes = b"".join(download.streaming_content)
        sizes = _page_sizes(downloaded_bytes)
        self.assertEqual(sizes[0], (300.0, 200.0))
        self.assertEqual(sizes[1], (150.0, 200.0))
        self.assertEqual(sizes[2], (300.0, 200.0))

        job = CropJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)

    def test_missing_file_is_rejected(self):
        response = self.client.post(
            "/api/v1/pdf/crop/", data={"x0": 0, "y0": 0, "x1": 1, "y1": 1}, format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("file", response.json()["errors"])

    def test_missing_rect_fields_are_rejected(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        response = self.client.post(
            "/api/v1/pdf/crop/", data={"file": pdf}, format="multipart",
        )
        self.assertEqual(response.status_code, 400)

    def test_zero_size_crop_is_rejected(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        response = self._post(pdf, 0.5, 0.5, 0.5, 0.5)
        self.assertEqual(response.status_code, 400)
        self.assertIn("crop_rect", response.json()["errors"])

    def test_negative_coordinate_is_rejected(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        response = self._post(pdf, -0.1, 0, 1, 1)
        self.assertEqual(response.status_code, 400)
        self.assertIn("crop_rect", response.json()["errors"])

    def test_oversized_coordinate_is_rejected(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        response = self._post(pdf, 0, 0, 1.2, 1)
        self.assertEqual(response.status_code, 400)
        self.assertIn("crop_rect", response.json()["errors"])

    def test_out_of_range_page_is_rejected(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self._post(pdf, 0, 0, 1, 1, pages=[99])
        self.assertEqual(response.status_code, 400)
        self.assertIn("pages", response.json()["errors"])

    def test_rejects_non_pdf_file(self):
        not_a_pdf = SimpleUploadedFile("evil.txt", b"hello", content_type="text/plain")
        response = self._post(not_a_pdf)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_rejects_fake_pdf_without_signature(self):
        fake = SimpleUploadedFile("fake.pdf", b"not a real pdf", content_type="application/pdf")
        response = self._post(fake)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_rejects_oversized_file_via_shared_validator(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        with self.assertRaises(serializers.ValidationError):
            validate_pdf_file(pdf, max_size=10)

    def test_no_page_lost_when_cropping_subset(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3", "P4"])
        response = self._post(pdf, 0, 0, 0.6, 0.6, pages=[2, 4])

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["data"]["page_count"], 4)

        download_url = body["data"]["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        downloaded_bytes = b"".join(download.streaming_content)
        self.assertEqual(len(_page_sizes(downloaded_bytes)), 4)

        job = CropJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)


class CropJobOwnershipTests(TestCase):
    """
    Access-control regression suite for the shared ownership layer, as
    exercised through Crop PDF's download endpoint. Mirrors the equivalent
    Merge/Split/Organize/RemovePages/Rotate ownership test classes - same
    shared apps.common.ownership/views layer.
    """

    def _create_job(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self.client.post(
            "/api/v1/pdf/crop/",
            data={"file": pdf, "x0": 0, "y0": 0, "x1": 0.5, "y1": 0.5},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["data"]

    def test_owner_can_download_with_correct_token(self):
        data = self._create_job()
        download_url = data["download_url"].replace("http://testserver", "")

        response = self.client.get(download_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

        job = CropJob.objects.get(id=data["file_id"])
        job.output_file.delete(save=False)

    def test_wrong_token_is_denied(self):
        data = self._create_job()
        job = CropJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/api/v1/pdf/crop/{job.id}/download/?token=not-the-real-token")

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

        job.output_file.delete(save=False)

    def test_missing_token_is_denied(self):
        data = self._create_job()
        job = CropJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/api/v1/pdf/crop/{job.id}/download/")

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

        job.output_file.delete(save=False)

    def test_nonexistent_job_id_returns_identical_response_shape_to_wrong_token(self):
        import uuid

        response = self.client.get(f"/api/v1/pdf/crop/{uuid.uuid4()}/download/?token=whatever")

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

    def test_malformed_job_id_is_handled_safely_not_a_500(self):
        response = self.client.get("/api/v1/pdf/crop/not-a-valid-uuid/download/?token=whatever")
        self.assertEqual(response.status_code, 404)

    def test_raw_media_path_does_not_serve_the_output_file(self):
        data = self._create_job()
        job = CropJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/media/crop/output/{job.id}.pdf")

        self.assertEqual(response.status_code, 404)

        job.output_file.delete(save=False)
