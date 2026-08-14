import fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import serializers

from apps.common.validation import validate_pdf_file

from .models import MergeJob
from .services import MergePDFService


def _make_pdf_file(name, page_texts, page_size=(300, 200)):
    """Build a small real PDF with one page per string in `page_texts`."""
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


class MergePDFServiceTests(TestCase):
    """Unit tests for the core merge logic, no HTTP/DB involved beyond MergeJob."""

    def test_merge_preserves_upload_order_and_all_pages(self):
        a = _make_pdf_file("a.pdf", ["A1", "A2"])
        b = _make_pdf_file("b.pdf", ["B1"])

        output_bytes, total_pages = MergePDFService.merge([a, b])

        self.assertEqual(total_pages, 3)
        self.assertEqual(_page_texts(output_bytes), ["A1", "A2", "B1"])

    def test_merge_with_custom_order(self):
        a = _make_pdf_file("a.pdf", ["A1"])
        b = _make_pdf_file("b.pdf", ["B1"])
        c = _make_pdf_file("c.pdf", ["C1"])

        output_bytes, total_pages = MergePDFService.merge([a, b, c], order=[2, 0, 1])

        self.assertEqual(total_pages, 3)
        self.assertEqual(_page_texts(output_bytes), ["C1", "A1", "B1"])

    def test_run_creates_completed_job_with_output_file(self):
        a = _make_pdf_file("a.pdf", ["A1"])
        b = _make_pdf_file("b.pdf", ["B1", "B2"])

        job = MergePDFService.run(user=None, files=[a, b])

        self.assertEqual(job.status, MergeJob.Status.COMPLETED)
        self.assertEqual(job.total_pages, 3)
        self.assertEqual(job.source_count, 2)
        self.assertEqual(job.source_filenames, ["a.pdf", "b.pdf"])
        self.assertTrue(job.output_file.name)

        job.output_file.delete(save=False)

    def test_run_marks_job_failed_on_unreadable_file(self):
        class ExplodingFile:
            name = "broken.pdf"

            def seek(self, *_args):
                pass

            def read(self, *_args):
                raise OSError("simulated read failure")

        job = MergePDFService.run(user=None, files=[ExplodingFile(), ExplodingFile()])

        self.assertEqual(job.status, MergeJob.Status.FAILED)
        self.assertIn("simulated read failure", job.error_message)
        self.assertFalse(job.output_file)


class MergePDFAPITests(TestCase):
    """API-level tests: validation, response envelope, and the full flow."""

    def _post(self, files, order=None):
        data = {"files": files}
        if order is not None:
            data["order"] = order
        return self.client.post("/api/v1/pdf/merge/", data=data, format="multipart")

    def test_successful_merge_returns_consistent_envelope_and_downloadable_file(self):
        a = _make_pdf_file("a.pdf", ["A1"])
        b = _make_pdf_file("b.pdf", ["B1", "B2"])

        response = self._post([a, b])

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["message"], "PDFs merged successfully")
        self.assertEqual(body["data"]["filename"], "merged.pdf")
        self.assertEqual(body["data"]["total_pages"], 3)
        self.assertEqual(body["data"]["source_count"], 2)

        download_url = body["data"]["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "application/pdf")
        downloaded_bytes = b"".join(download.streaming_content)
        self.assertEqual(_page_texts(downloaded_bytes), ["A1", "B1", "B2"])

        job = MergeJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)

    def test_merge_with_explicit_order_via_api(self):
        a = _make_pdf_file("a.pdf", ["A1"])
        b = _make_pdf_file("b.pdf", ["B1"])

        response = self._post([a, b], order=[1, 0])

        self.assertEqual(response.status_code, 201)
        body = response.json()
        download_url = body["data"]["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        downloaded_bytes = b"".join(download.streaming_content)
        self.assertEqual(_page_texts(downloaded_bytes), ["B1", "A1"])

        job = MergeJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)

    def test_rejects_single_file(self):
        a = _make_pdf_file("a.pdf", ["A1"])

        response = self._post([a])

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "VALIDATION_ERROR")
        self.assertIn("files", body["errors"])

    def test_rejects_non_pdf_file(self):
        a = _make_pdf_file("a.pdf", ["A1"])
        not_a_pdf = SimpleUploadedFile("evil.txt", b"just some text", content_type="text/plain")

        response = self._post([a, not_a_pdf])

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_rejects_file_with_pdf_extension_but_no_pdf_signature(self):
        a = _make_pdf_file("a.pdf", ["A1"])
        fake = SimpleUploadedFile("fake.pdf", b"not really a pdf", content_type="application/pdf")

        response = self._post([a, fake])

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_rejects_invalid_order_permutation(self):
        a = _make_pdf_file("a.pdf", ["A1"])
        b = _make_pdf_file("b.pdf", ["B1"])

        response = self._post([a, b], order=[0, 0])

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertIn("order", body["errors"])

    def test_rejects_too_many_files(self):
        files = [_make_pdf_file(f"{i}.pdf", [f"P{i}"]) for i in range(21)]

        response = self._post(files)

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_rejects_oversized_file(self):
        # A real 50MB+ fixture isn't worth constructing for this; the
        # size cap is exercised directly against the shared validator
        # (also used by MergePDFRequestSerializer), with an artificially
        # low ceiling, instead of round-tripping a huge file over HTTP.
        pdf = _make_pdf_file("a.pdf", ["A1"])

        with self.assertRaises(serializers.ValidationError):
            validate_pdf_file(pdf, max_size=10)


class MergeJobOwnershipTests(TestCase):
    """
    Access-control regression suite for the shared ownership layer
    (apps.common.ownership / apps.common.views.OwnedJobDownloadView), as
    exercised through Merge PDF's download endpoint.
    """

    def _create_job(self):
        a = _make_pdf_file("a.pdf", ["A1"])
        b = _make_pdf_file("b.pdf", ["B1"])
        response = self.client.post(
            "/api/v1/pdf/merge/", data={"files": [a, b]}, format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["data"]

    def test_owner_can_download_with_correct_token(self):
        data = self._create_job()
        download_url = data["download_url"].replace("http://testserver", "")

        response = self.client.get(download_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

        job = MergeJob.objects.get(id=data["file_id"])
        job.output_file.delete(save=False)

    def test_wrong_token_is_denied(self):
        data = self._create_job()
        job = MergeJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/api/v1/pdf/merge/{job.id}/download/?token=not-the-real-token")

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

        job.output_file.delete(save=False)

    def test_missing_token_is_denied(self):
        data = self._create_job()
        job = MergeJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/api/v1/pdf/merge/{job.id}/download/")

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

        job.output_file.delete(save=False)

    def test_nonexistent_job_id_returns_identical_response_shape_to_wrong_token(self):
        """
        Non-existence and "wrong token" must be indistinguishable to the
        caller - otherwise the endpoint becomes an oracle for discovering
        which job ids are real. Both are 404 with the same error_code.
        """
        import uuid

        response = self.client.get(f"/api/v1/pdf/merge/{uuid.uuid4()}/download/?token=whatever")

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

    def test_malformed_job_id_is_handled_safely_not_a_500(self):
        response = self.client.get("/api/v1/pdf/merge/not-a-valid-uuid/download/?token=whatever")
        self.assertEqual(response.status_code, 404)

    def test_raw_media_path_no_longer_serves_the_output_file(self):
        """
        Regression guard for the actual vulnerability this layer closes:
        before it, the output file was reachable directly under /media/
        with no token/ownership check at all, given only the job's id
        (which appears in the API response, and previously in the raw
        filename too).
        """
        data = self._create_job()
        job = MergeJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/media/merges/output/{job.id}.pdf")

        self.assertEqual(response.status_code, 404)

        job.output_file.delete(save=False)

    def test_inline_disposition_param_is_honored(self):
        data = self._create_job()
        download_url = data["download_url"].replace("http://testserver", "")

        response = self.client.get(f"{download_url}&disposition=inline")

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("attachment", response.get("Content-Disposition", ""))

        job = MergeJob.objects.get(id=data["file_id"])
        job.output_file.delete(save=False)

    def test_default_disposition_is_attachment(self):
        data = self._create_job()
        download_url = data["download_url"].replace("http://testserver", "")

        response = self.client.get(download_url)

        self.assertIn("attachment", response.get("Content-Disposition", ""))

        job = MergeJob.objects.get(id=data["file_id"])
        job.output_file.delete(save=False)
