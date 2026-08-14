import fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import serializers

from apps.common.validation import validate_pdf_file

from .models import RemovePagesJob
from .services import RemovePagesError, RemovePagesService, validate_pages_to_remove


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


class ValidatePagesToRemoveTests(TestCase):

    def test_remove_single_page_is_valid(self):
        validate_pages_to_remove([2], 5)  # must not raise

    def test_remove_multiple_pages_is_valid(self):
        validate_pages_to_remove([2, 4], 5)

    def test_remove_first_page_is_valid(self):
        validate_pages_to_remove([1], 5)

    def test_remove_last_page_is_valid(self):
        validate_pages_to_remove([5], 5)

    def test_remove_middle_pages_is_valid(self):
        validate_pages_to_remove([2, 3], 5)

    def test_remove_all_but_one_page_is_valid(self):
        validate_pages_to_remove([1, 2, 3, 4], 5)

    def test_rejects_out_of_range_page(self):
        with self.assertRaises(RemovePagesError):
            validate_pages_to_remove([99], 5)

    def test_rejects_duplicate_page(self):
        with self.assertRaises(RemovePagesError):
            validate_pages_to_remove([2, 2], 5)

    def test_rejects_removing_every_page(self):
        with self.assertRaises(RemovePagesError):
            validate_pages_to_remove([1, 2, 3, 4, 5], 5)

    def test_rejects_non_integer_value(self):
        with self.assertRaises(RemovePagesError):
            validate_pages_to_remove(["2"], 5)

    def test_rejects_empty_list(self):
        with self.assertRaises(RemovePagesError):
            validate_pages_to_remove([], 5)

    def test_rejects_zero_as_page_number(self):
        with self.assertRaises(RemovePagesError):
            validate_pages_to_remove([0], 5)

    def test_rejects_bool_masquerading_as_int(self):
        with self.assertRaises(RemovePagesError):
            validate_pages_to_remove([True], 5)

    def test_single_page_document_cannot_remove_its_only_page(self):
        with self.assertRaises(RemovePagesError):
            validate_pages_to_remove([1], 1)


class RemovePagesServiceTests(TestCase):

    def test_remove_pages_preserves_order_of_remaining_pages(self):
        pdf = _make_pdf_file("doc.pdf", [
            "ORIGINAL PAGE 1", "ORIGINAL PAGE 2", "ORIGINAL PAGE 3",
            "ORIGINAL PAGE 4", "ORIGINAL PAGE 5",
        ])
        output_bytes, source_count, output_count = RemovePagesService.remove_pages(
            pdf.read(), [2, 4],
        )

        self.assertEqual(source_count, 5)
        self.assertEqual(output_count, 3)
        self.assertEqual(
            _page_texts(output_bytes),
            ["ORIGINAL PAGE 1", "ORIGINAL PAGE 3", "ORIGINAL PAGE 5"],
        )

    def test_remove_first_page(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        output_bytes, _, output_count = RemovePagesService.remove_pages(pdf.read(), [1])
        self.assertEqual(output_count, 2)
        self.assertEqual(_page_texts(output_bytes), ["P2", "P3"])

    def test_remove_last_page(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        output_bytes, _, output_count = RemovePagesService.remove_pages(pdf.read(), [3])
        self.assertEqual(output_count, 2)
        self.assertEqual(_page_texts(output_bytes), ["P1", "P2"])

    def test_run_creates_completed_job(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        job = RemovePagesService.run(user=None, uploaded_file=pdf, pages_to_remove=[2])

        self.assertEqual(job.status, RemovePagesJob.Status.COMPLETED)
        self.assertEqual(job.source_page_count, 3)
        self.assertEqual(job.output_page_count, 2)
        self.assertEqual(job.removed_pages, [2])
        self.assertTrue(job.output_file.name)

        job.output_file.delete(save=False)

    def test_run_deletes_job_and_raises_when_removing_all_pages(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        with self.assertRaises(RemovePagesError):
            RemovePagesService.run(user=None, uploaded_file=pdf, pages_to_remove=[1, 2])
        self.assertEqual(RemovePagesJob.objects.count(), 0)


class RemovePagesAPITests(TestCase):

    def _post(self, pdf, pages):
        return self.client.post(
            "/api/v1/pdf/remove-pages/", data={"file": pdf, "pages": pages}, format="multipart",
        )

    def test_valid_removal_end_to_end(self):
        pdf = _make_pdf_file("doc.pdf", [
            "ORIGINAL PAGE 1", "ORIGINAL PAGE 2", "ORIGINAL PAGE 3",
            "ORIGINAL PAGE 4", "ORIGINAL PAGE 5",
        ])

        response = self._post(pdf, [2, 4])

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["message"], "Pages removed successfully")
        self.assertEqual(body["data"]["source_page_count"], 5)
        self.assertEqual(body["data"]["output_page_count"], 3)
        self.assertEqual(body["data"]["removed_pages"], [2, 4])

        download_url = body["data"]["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "application/pdf")

        downloaded_bytes = b"".join(download.streaming_content)
        self.assertEqual(
            _page_texts(downloaded_bytes),
            ["ORIGINAL PAGE 1", "ORIGINAL PAGE 3", "ORIGINAL PAGE 5"],
        )

        job = RemovePagesJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)

    def test_missing_file_is_rejected(self):
        response = self.client.post(
            "/api/v1/pdf/remove-pages/", data={"pages": [1]}, format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("file", response.json()["errors"])

    def test_missing_pages_is_rejected(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self.client.post(
            "/api/v1/pdf/remove-pages/", data={"file": pdf}, format="multipart",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("pages", response.json()["errors"])

    def test_invalid_page_number_returns_400(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self._post(pdf, [99])
        self.assertEqual(response.status_code, 400)
        self.assertIn("pages", response.json()["errors"])

    def test_duplicate_page_returns_400(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        response = self._post(pdf, [1, 1])
        self.assertEqual(response.status_code, 400)
        self.assertIn("pages", response.json()["errors"])

    def test_removing_every_page_returns_400(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self._post(pdf, [1, 2])
        self.assertEqual(response.status_code, 400)
        self.assertIn("pages", response.json()["errors"])

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

    def test_two_page_pdf_remove_one_leaves_valid_single_page_pdf(self):
        pdf = _make_pdf_file("doc.pdf", ["First", "Second"])
        response = self._post(pdf, [1])

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["data"]["output_page_count"], 1)

        download_url = body["data"]["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        downloaded_bytes = b"".join(download.streaming_content)
        self.assertEqual(_page_texts(downloaded_bytes), ["Second"])

        job = RemovePagesJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)


class RemovePagesJobOwnershipTests(TestCase):
    """
    Access-control regression suite for the shared ownership layer, as
    exercised through Remove Pages' download endpoint. Mirrors the
    equivalent Merge/Split/Organize ownership test classes - same shared
    apps.common.ownership/views layer.
    """

    def _create_job(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        response = self.client.post(
            "/api/v1/pdf/remove-pages/", data={"file": pdf, "pages": [2]}, format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["data"]

    def test_owner_can_download_with_correct_token(self):
        data = self._create_job()
        download_url = data["download_url"].replace("http://testserver", "")

        response = self.client.get(download_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

        job = RemovePagesJob.objects.get(id=data["file_id"])
        job.output_file.delete(save=False)

    def test_wrong_token_is_denied(self):
        data = self._create_job()
        job = RemovePagesJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/api/v1/pdf/remove-pages/{job.id}/download/?token=not-the-real-token")

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

        job.output_file.delete(save=False)

    def test_missing_token_is_denied(self):
        data = self._create_job()
        job = RemovePagesJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/api/v1/pdf/remove-pages/{job.id}/download/")

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

        job.output_file.delete(save=False)

    def test_nonexistent_job_id_returns_identical_response_shape_to_wrong_token(self):
        import uuid

        response = self.client.get(f"/api/v1/pdf/remove-pages/{uuid.uuid4()}/download/?token=whatever")

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

    def test_malformed_job_id_is_handled_safely_not_a_500(self):
        response = self.client.get("/api/v1/pdf/remove-pages/not-a-valid-uuid/download/?token=whatever")
        self.assertEqual(response.status_code, 404)

    def test_raw_media_path_does_not_serve_the_output_file(self):
        data = self._create_job()
        job = RemovePagesJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/media/remove_pages/output/{job.id}.pdf")

        self.assertEqual(response.status_code, 404)

        job.output_file.delete(save=False)
