import fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import serializers

from apps.common.validation import validate_pdf_file

from .models import OrganizeJob
from .services import OrganizeError, OrganizePDFService, validate_order


def _make_pdf_file(name, page_texts, page_size=(300, 200)):
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page(width=page_size[0], height=page_size[1])
        page.insert_text(fitz.Point(50, 100), text, fontsize=14)
    data = doc.tobytes()
    doc.close()
    return SimpleUploadedFile(name, data, content_type="application/pdf")


def _page_texts(pdf_bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        return [page.get_text().strip() for page in doc]
    finally:
        doc.close()


class ValidateOrderTests(TestCase):
    """Unit tests #1-13 from the spec: order validation edge cases."""

    def test_normal_reorder_is_valid(self):
        validate_order([2, 1, 3], 3)  # must not raise

    def test_reverse_order_is_valid(self):
        validate_order([5, 4, 3, 2, 1], 5)

    def test_random_order_is_valid(self):
        validate_order([3, 1, 5, 2, 4], 5)

    def test_first_page_moved_to_end_is_valid(self):
        validate_order([2, 3, 4, 5, 1], 5)

    def test_last_page_moved_to_beginning_is_valid(self):
        validate_order([5, 1, 2, 3, 4], 5)

    def test_single_page_document_order_is_valid(self):
        validate_order([1], 1)

    def test_two_page_document_swap_is_valid(self):
        validate_order([2, 1], 2)

    def test_rejects_out_of_range_page_number(self):
        with self.assertRaises(OrganizeError):
            validate_order([1, 2, 3, 4, 99], 5)

    def test_rejects_duplicate_page(self):
        with self.assertRaises(OrganizeError):
            validate_order([1, 2, 2, 4, 5], 5)

    def test_rejects_missing_page(self):
        with self.assertRaises(OrganizeError):
            validate_order([3, 1, 5, 2], 5)  # missing page 4, only 4 entries

    def test_rejects_non_integer_value(self):
        with self.assertRaises(OrganizeError):
            validate_order([1, 2, "3", 4, 5], 5)

    def test_rejects_empty_order(self):
        with self.assertRaises(OrganizeError):
            validate_order([], 5)

    def test_rejects_wrong_length_even_if_values_look_plausible(self):
        with self.assertRaises(OrganizeError):
            validate_order([1, 2, 3], 5)

    def test_rejects_zero_as_page_number(self):
        """API is 1-based - 0 is out of range, not a valid first page."""
        with self.assertRaises(OrganizeError):
            validate_order([0, 1, 2], 3)

    def test_rejects_bool_masquerading_as_int(self):
        """bool is a subclass of int in Python - must not silently pass."""
        with self.assertRaises(OrganizeError):
            validate_order([True, 2, 3], 3)


class OrganizePDFServiceTests(TestCase):
    """Service-level reorder tests, verifying actual PDF content/order."""

    def test_organize_applies_exact_requested_order(self):
        pdf = _make_pdf_file("doc.pdf", [
            "ORIGINAL PAGE 1", "ORIGINAL PAGE 2", "ORIGINAL PAGE 3",
            "ORIGINAL PAGE 4", "ORIGINAL PAGE 5",
        ])
        output_bytes, page_count = OrganizePDFService.organize(pdf.read(), [5, 2, 4, 1, 3])

        self.assertEqual(page_count, 5)
        self.assertEqual(
            _page_texts(output_bytes),
            ["ORIGINAL PAGE 5", "ORIGINAL PAGE 2", "ORIGINAL PAGE 4", "ORIGINAL PAGE 1", "ORIGINAL PAGE 3"],
        )

    def test_organize_rejects_invalid_order_via_service_too(self):
        """Defense in depth: the service itself validates, not just the
        serializer, in case something ever calls it directly."""
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        with self.assertRaises(OrganizeError):
            OrganizePDFService.organize(pdf.read(), [1, 1])

    def test_run_creates_completed_job(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        job = OrganizePDFService.run(user=None, uploaded_file=pdf, order=[3, 1, 2])

        self.assertEqual(job.status, OrganizeJob.Status.COMPLETED)
        self.assertEqual(job.page_count, 3)
        self.assertEqual(job.page_order, [3, 1, 2])
        self.assertTrue(job.output_file.name)

        job.output_file.delete(save=False)

    def test_run_deletes_job_and_raises_on_invalid_order(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        with self.assertRaises(OrganizeError):
            OrganizePDFService.run(user=None, uploaded_file=pdf, order=[1, 1])
        self.assertEqual(OrganizeJob.objects.count(), 0)


class OrganizePDFAPITests(TestCase):
    """API-level tests: valid/invalid requests, output verification."""

    def _post(self, pdf, order):
        return self.client.post(
            "/api/v1/pdf/organize/", data={"file": pdf, "order": order}, format="multipart",
        )

    def test_valid_reorder_end_to_end(self):
        pdf = _make_pdf_file("doc.pdf", [
            "ORIGINAL PAGE 1", "ORIGINAL PAGE 2", "ORIGINAL PAGE 3",
            "ORIGINAL PAGE 4", "ORIGINAL PAGE 5",
        ])

        response = self._post(pdf, [5, 2, 4, 1, 3])

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["message"], "PDF organized successfully")
        self.assertEqual(body["data"]["page_count"], 5)
        self.assertEqual(body["data"]["filename"], "organized.pdf")

        download_url = body["data"]["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "application/pdf")

        downloaded_bytes = b"".join(download.streaming_content)
        self.assertEqual(
            _page_texts(downloaded_bytes),
            ["ORIGINAL PAGE 5", "ORIGINAL PAGE 2", "ORIGINAL PAGE 4", "ORIGINAL PAGE 1", "ORIGINAL PAGE 3"],
        )

        job = OrganizeJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)

    def test_missing_file_is_rejected(self):
        response = self.client.post(
            "/api/v1/pdf/organize/", data={"order": [1, 2]}, format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertIn("file", body["errors"])

    def test_missing_order_is_rejected(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self.client.post(
            "/api/v1/pdf/organize/", data={"file": pdf}, format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("order", response.json()["errors"])

    def test_invalid_order_out_of_range_returns_400(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self._post(pdf, [1, 99])
        self.assertEqual(response.status_code, 400)
        self.assertIn("order", response.json()["errors"])

    def test_duplicate_page_in_order_returns_400(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        response = self._post(pdf, [1, 1, 3])
        self.assertEqual(response.status_code, 400)
        self.assertIn("order", response.json()["errors"])

    def test_missing_page_in_order_returns_400(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        response = self._post(pdf, [1, 2])
        self.assertEqual(response.status_code, 400)
        self.assertIn("order", response.json()["errors"])

    def test_non_integer_order_value_returns_400(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self._post(pdf, ["a", "b"])
        self.assertEqual(response.status_code, 400)
        self.assertIn("order", response.json()["errors"])

    def test_rejects_non_pdf_file(self):
        not_a_pdf = SimpleUploadedFile("evil.txt", b"hello", content_type="text/plain")
        response = self._post(not_a_pdf, [1])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_rejects_fake_pdf_without_signature(self):
        fake = SimpleUploadedFile("fake.pdf", b"not a real pdf", content_type="application/pdf")
        response = self._post(fake, [1])
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_rejects_oversized_file_via_shared_validator(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        with self.assertRaises(serializers.ValidationError):
            validate_pdf_file(pdf, max_size=10)

    def test_single_page_pdf_reorder_is_a_noop(self):
        pdf = _make_pdf_file("doc.pdf", ["Only page"])
        response = self._post(pdf, [1])

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["data"]["page_count"], 1)

        job = OrganizeJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)

    def test_two_page_pdf_swap(self):
        pdf = _make_pdf_file("doc.pdf", ["First", "Second"])
        response = self._post(pdf, [2, 1])

        self.assertEqual(response.status_code, 201)
        body = response.json()
        download_url = body["data"]["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        downloaded_bytes = b"".join(download.streaming_content)
        self.assertEqual(_page_texts(downloaded_bytes), ["Second", "First"])

        job = OrganizeJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)


class OrganizeJobOwnershipTests(TestCase):
    """
    Access-control regression suite for the shared ownership layer, as
    exercised through Organize PDF's download endpoint. Mirrors
    apps.pdf_merge.tests.MergeJobOwnershipTests / apps.pdf_split's
    equivalent - same shared apps.common.ownership/views layer.
    """

    def _create_job(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        response = self.client.post(
            "/api/v1/pdf/organize/", data={"file": pdf, "order": [3, 1, 2]}, format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["data"]

    def test_owner_can_download_with_correct_token(self):
        data = self._create_job()
        download_url = data["download_url"].replace("http://testserver", "")

        response = self.client.get(download_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

        job = OrganizeJob.objects.get(id=data["file_id"])
        job.output_file.delete(save=False)

    def test_wrong_token_is_denied(self):
        data = self._create_job()
        job = OrganizeJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/api/v1/pdf/organize/{job.id}/download/?token=not-the-real-token")

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

        job.output_file.delete(save=False)

    def test_missing_token_is_denied(self):
        data = self._create_job()
        job = OrganizeJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/api/v1/pdf/organize/{job.id}/download/")

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

        job.output_file.delete(save=False)

    def test_nonexistent_job_id_returns_identical_response_shape_to_wrong_token(self):
        import uuid

        response = self.client.get(f"/api/v1/pdf/organize/{uuid.uuid4()}/download/?token=whatever")

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

    def test_malformed_job_id_is_handled_safely_not_a_500(self):
        response = self.client.get("/api/v1/pdf/organize/not-a-valid-uuid/download/?token=whatever")
        self.assertEqual(response.status_code, 404)

    def test_raw_media_path_does_not_serve_the_output_file(self):
        data = self._create_job()
        job = OrganizeJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/media/organize/output/{job.id}.pdf")

        self.assertEqual(response.status_code, 404)

        job.output_file.delete(save=False)
