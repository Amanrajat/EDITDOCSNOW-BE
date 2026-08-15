import fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import serializers

from apps.common.validation import validate_pdf_file

from .models import RotateJob
from .services import RotateError, RotatePDFService, validate_degrees, validate_pages


def _make_pdf_file(name, page_texts, page_size=(300, 200)):
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page(width=page_size[0], height=page_size[1])
        page.insert_text(fitz.Point(50, 100), text, fontsize=14)
    data = doc.tobytes()
    doc.close()
    return SimpleUploadedFile(name, data, content_type="application/pdf")


def _page_rotations(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [page.rotation for page in doc]
    finally:
        doc.close()


def _page_texts(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [page.get_text().strip() for page in doc]
    finally:
        doc.close()


class ValidateDegreesTests(TestCase):

    def test_accepts_90(self):
        validate_degrees(90)

    def test_accepts_180(self):
        validate_degrees(180)

    def test_accepts_270(self):
        validate_degrees(270)

    def test_accepts_negative_90(self):
        validate_degrees(-90)

    def test_rejects_zero(self):
        with self.assertRaises(RotateError):
            validate_degrees(0)

    def test_rejects_360(self):
        with self.assertRaises(RotateError):
            validate_degrees(360)

    def test_rejects_non_multiple_of_90(self):
        with self.assertRaises(RotateError):
            validate_degrees(45)

    def test_rejects_non_integer(self):
        with self.assertRaises(RotateError):
            validate_degrees("90")

    def test_rejects_bool(self):
        with self.assertRaises(RotateError):
            validate_degrees(True)


class ValidatePagesTests(TestCase):

    def test_none_means_all_pages_and_is_valid(self):
        validate_pages(None, 5)

    def test_valid_page_subset(self):
        validate_pages([1, 3], 5)

    def test_rejects_out_of_range(self):
        with self.assertRaises(RotateError):
            validate_pages([99], 5)

    def test_rejects_duplicate(self):
        with self.assertRaises(RotateError):
            validate_pages([1, 1], 5)

    def test_rejects_non_integer(self):
        with self.assertRaises(RotateError):
            validate_pages(["1"], 5)


class RotatePDFServiceTests(TestCase):

    def test_rotate_single_page_90(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        output_bytes, page_count, rotated = RotatePDFService.rotate(pdf.read(), [2], 90)

        self.assertEqual(page_count, 3)
        self.assertEqual(rotated, [2])
        self.assertEqual(_page_rotations(output_bytes), [0, 90, 0])
        # Text content must be untouched - only orientation changes.
        self.assertEqual(_page_texts(output_bytes), ["P1", "P2", "P3"])

    def test_rotate_multiple_pages_180(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        output_bytes, _, rotated = RotatePDFService.rotate(pdf.read(), [1, 3], 180)

        self.assertEqual(rotated, [1, 3])
        self.assertEqual(_page_rotations(output_bytes), [180, 0, 180])

    def test_rotate_all_pages_270(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        output_bytes, page_count, rotated = RotatePDFService.rotate(pdf.read(), None, 270)

        self.assertEqual(rotated, [1, 2, 3])
        self.assertEqual(_page_rotations(output_bytes), [270, 270, 270])

    def test_negative_90_normalizes_to_270(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        output_bytes, _, _ = RotatePDFService.rotate(pdf.read(), [1], -90)
        self.assertEqual(_page_rotations(output_bytes), [270])

    def test_rotating_twice_by_90_compounds_to_180(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        first_bytes, _, _ = RotatePDFService.rotate(pdf.read(), [1], 90)
        second_bytes, _, _ = RotatePDFService.rotate(first_bytes, [1], 90)
        self.assertEqual(_page_rotations(second_bytes), [180])

    def test_run_creates_completed_job(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        job = RotatePDFService.run(user=None, uploaded_file=pdf, pages=[1], degrees=90)

        self.assertEqual(job.status, RotateJob.Status.COMPLETED)
        self.assertEqual(job.page_count, 2)
        self.assertEqual(job.rotated_pages, [1])
        self.assertEqual(job.degrees, 90)
        self.assertTrue(job.output_file.name)

        job.output_file.delete(save=False)

    def test_run_deletes_job_and_raises_on_invalid_degrees(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        with self.assertRaises(RotateError):
            RotatePDFService.run(user=None, uploaded_file=pdf, pages=None, degrees=45)
        self.assertEqual(RotateJob.objects.count(), 0)


class RotatePDFAPITests(TestCase):

    def _post(self, pdf, degrees, pages=None):
        data = {"file": pdf, "degrees": degrees}
        if pages is not None:
            data["pages"] = pages
        return self.client.post("/api/v1/pdf/rotate/", data=data, format="multipart")

    def test_rotate_all_pages_end_to_end(self):
        pdf = _make_pdf_file("doc.pdf", ["ORIGINAL PAGE 1", "ORIGINAL PAGE 2"])
        response = self._post(pdf, 90)

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["message"], "PDF rotated successfully")
        self.assertEqual(body["data"]["page_count"], 2)
        self.assertEqual(body["data"]["rotated_pages"], [1, 2])
        self.assertEqual(body["data"]["degrees"], 90)

        download_url = body["data"]["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "application/pdf")

        downloaded_bytes = b"".join(download.streaming_content)
        self.assertEqual(_page_rotations(downloaded_bytes), [90, 90])
        self.assertEqual(_page_texts(downloaded_bytes), ["ORIGINAL PAGE 1", "ORIGINAL PAGE 2"])

        job = RotateJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)

    def test_rotate_specific_pages_180(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        response = self._post(pdf, 180, pages=[2])

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["data"]["rotated_pages"], [2])

        download_url = body["data"]["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        downloaded_bytes = b"".join(download.streaming_content)
        self.assertEqual(_page_rotations(downloaded_bytes), [0, 180, 0])

        job = RotateJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)

    def test_rotate_270(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        response = self._post(pdf, 270, pages=[1])

        self.assertEqual(response.status_code, 201)
        body = response.json()
        download_url = body["data"]["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        downloaded_bytes = b"".join(download.streaming_content)
        self.assertEqual(_page_rotations(downloaded_bytes), [270])

        job = RotateJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)

    def test_missing_file_is_rejected(self):
        response = self.client.post(
            "/api/v1/pdf/rotate/", data={"degrees": 90}, format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("file", response.json()["errors"])

    def test_missing_degrees_is_rejected(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        response = self.client.post(
            "/api/v1/pdf/rotate/", data={"file": pdf}, format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("degrees", response.json()["errors"])

    def test_zero_degrees_is_rejected(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        response = self._post(pdf, 0)
        self.assertEqual(response.status_code, 400)
        self.assertIn("degrees", response.json()["errors"])

    def test_non_multiple_of_90_is_rejected(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        response = self._post(pdf, 45)
        self.assertEqual(response.status_code, 400)
        self.assertIn("degrees", response.json()["errors"])

    def test_out_of_range_page_is_rejected(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self._post(pdf, 90, pages=[99])
        self.assertEqual(response.status_code, 400)
        self.assertIn("pages", response.json()["errors"])

    def test_rejects_non_pdf_file(self):
        not_a_pdf = SimpleUploadedFile("evil.txt", b"hello", content_type="text/plain")
        response = self._post(not_a_pdf, 90)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_rejects_fake_pdf_without_signature(self):
        fake = SimpleUploadedFile("fake.pdf", b"not a real pdf", content_type="application/pdf")
        response = self._post(fake, 90)
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_rejects_oversized_file_via_shared_validator(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        with self.assertRaises(serializers.ValidationError):
            validate_pdf_file(pdf, max_size=10)

    def test_single_page_pdf_rotation(self):
        pdf = _make_pdf_file("doc.pdf", ["Only page"])
        response = self._post(pdf, 90)

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["data"]["page_count"], 1)

        job = RotateJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)


class RotateJobOwnershipTests(TestCase):
    """
    Access-control regression suite for the shared ownership layer, as
    exercised through Rotate PDF's download endpoint. Mirrors the
    equivalent Merge/Split/Organize/RemovePages ownership test classes -
    same shared apps.common.ownership/views layer.
    """

    def _create_job(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self.client.post(
            "/api/v1/pdf/rotate/", data={"file": pdf, "degrees": 90}, format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["data"]

    def test_owner_can_download_with_correct_token(self):
        data = self._create_job()
        download_url = data["download_url"].replace("http://testserver", "")

        response = self.client.get(download_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

        job = RotateJob.objects.get(id=data["file_id"])
        job.output_file.delete(save=False)

    def test_wrong_token_is_denied(self):
        data = self._create_job()
        job = RotateJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/api/v1/pdf/rotate/{job.id}/download/?token=not-the-real-token")

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

        job.output_file.delete(save=False)

    def test_missing_token_is_denied(self):
        data = self._create_job()
        job = RotateJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/api/v1/pdf/rotate/{job.id}/download/")

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

        job.output_file.delete(save=False)

    def test_nonexistent_job_id_returns_identical_response_shape_to_wrong_token(self):
        import uuid

        response = self.client.get(f"/api/v1/pdf/rotate/{uuid.uuid4()}/download/?token=whatever")

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

    def test_malformed_job_id_is_handled_safely_not_a_500(self):
        response = self.client.get("/api/v1/pdf/rotate/not-a-valid-uuid/download/?token=whatever")
        self.assertEqual(response.status_code, 404)

    def test_raw_media_path_does_not_serve_the_output_file(self):
        data = self._create_job()
        job = RotateJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/media/rotate/output/{job.id}.pdf")

        self.assertEqual(response.status_code, 404)

        job.output_file.delete(save=False)
