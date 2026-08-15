import shutil
import unittest

import fitz
from django.core.files.base import ContentFile
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .models import OcrJob
from .services import OcrError, SUPPORTED_LANGUAGES, create_job, process_job, run_ocr, validate_language

EAGER = override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)

_TESSERACT_AVAILABLE = shutil.which("tesseract") is not None
_SKIP_REASON = "Tesseract ('tesseract') is not installed in this environment"


def _make_text_pdf(text, page_size=(612, 792)):
    doc = fitz.open()
    page = doc.new_page(width=page_size[0], height=page_size[1])
    page.insert_text((72, 200), text, fontsize=28, fontname="Helvetica-Bold")
    data = doc.tobytes()
    doc.close()
    return data


def _make_scanned_pdf(text, page_size=(612, 792)):
    """A genuinely scanned-like PDF: real text rendered to a page, then
    that page is rasterized to an image and re-embedded as the ONLY
    content of a fresh page - no real text layer at all, same as a
    photographed/scanned document. OCR has to actually recognize `text`
    from pixels for these tests to mean anything."""
    text_doc = fitz.open()
    text_page = text_doc.new_page(width=page_size[0], height=page_size[1])
    text_page.insert_text((72, 200), text, fontsize=28, fontname="Helvetica-Bold")
    pix = text_page.get_pixmap(dpi=200)
    png_bytes = pix.tobytes("png")
    text_doc.close()

    scanned_doc = fitz.open()
    page = scanned_doc.new_page(width=page_size[0], height=page_size[1])
    page.insert_image(fitz.Rect(0, 0, page_size[0], page_size[1]), stream=png_bytes)
    data = scanned_doc.tobytes()
    scanned_doc.close()
    return data


class ValidateLanguageTests(TestCase):

    def test_accepts_single_supported_language(self):
        self.assertEqual(validate_language("eng"), ["eng"])

    def test_accepts_multiple_languages(self):
        self.assertEqual(validate_language("eng+fra"), ["eng", "fra"])

    def test_rejects_unsupported_language(self):
        with self.assertRaises(OcrError):
            validate_language("xyz")

    def test_rejects_one_bad_code_in_a_combination(self):
        with self.assertRaises(OcrError):
            validate_language("eng+xyz")

    def test_rejects_empty_string(self):
        with self.assertRaises(OcrError):
            validate_language("")

    def test_all_advertised_languages_are_internally_consistent(self):
        for code in SUPPORTED_LANGUAGES:
            validate_language(code)  # must not raise


class CreateJobTests(TestCase):

    def test_creates_queued_job_with_source_file_saved(self):
        pdf = SimpleUploadedFile("doc.pdf", _make_text_pdf("Hello"), content_type="application/pdf")
        job = create_job(user=None, uploaded_file=pdf, language="eng")

        self.assertEqual(job.status, OcrJob.Status.QUEUED)
        self.assertEqual(job.language, "eng")
        self.assertTrue(job.source_file.name)
        self.assertTrue(job.owner_token)

        job.source_file.delete(save=False)

    def test_rejects_unsupported_language_without_creating_a_job(self):
        pdf = SimpleUploadedFile("doc.pdf", _make_text_pdf("Hello"), content_type="application/pdf")
        before_count = OcrJob.objects.count()
        with self.assertRaises(OcrError):
            create_job(user=None, uploaded_file=pdf, language="xyz")
        self.assertEqual(OcrJob.objects.count(), before_count)


@unittest.skipUnless(_TESSERACT_AVAILABLE, _SKIP_REASON)
class RunOcrServiceTests(TestCase):
    """
    Runs for real only where Tesseract is actually installed (the
    production Docker image - see Dockerfile). Skipped, not silently
    passed, everywhere else so CI output makes clear these were never
    executed rather than looking green by accident.
    """

    def test_scanned_page_becomes_searchable(self):
        data = _make_scanned_pdf("HELLOWORLD")
        output_bytes, metadata = run_ocr(data, language="eng")

        self.assertEqual(metadata["page_count"], 1)
        self.assertEqual(metadata["ocr_page_count"], 1)

        doc = fitz.open(stream=output_bytes, filetype="pdf")
        extracted = doc[0].get_text().upper()
        doc.close()
        self.assertIn("HELLOWORLD", extracted.replace(" ", ""))

    def test_page_with_real_text_is_left_alone(self):
        data = _make_text_pdf("Already has real text")
        output_bytes, metadata = run_ocr(data, language="eng")

        self.assertEqual(metadata["ocr_page_count"], 0)
        doc = fitz.open(stream=output_bytes, filetype="pdf")
        self.assertIn("Already has real text", doc[0].get_text())
        doc.close()

    def test_rejects_unsupported_language(self):
        data = _make_scanned_pdf("Text")
        with self.assertRaises(OcrError):
            run_ocr(data, language="xyz")

    def test_process_job_completes_and_cleans_up_source(self):
        pdf = SimpleUploadedFile("doc.pdf", _make_scanned_pdf("SCANNEDTEXT"), content_type="application/pdf")
        job = create_job(user=None, uploaded_file=pdf, language="eng")

        process_job(str(job.id))

        job.refresh_from_db()
        self.assertEqual(job.status, OcrJob.Status.COMPLETED)
        self.assertTrue(job.output_file.name)
        self.assertFalse(job.source_file)  # cleaned up
        self.assertIsNotNone(job.completed_at)

        job.output_file.delete(save=False)


@EAGER
@unittest.skipUnless(_TESSERACT_AVAILABLE, _SKIP_REASON)
class OcrAPITests(TestCase):

    def test_submit_and_poll_until_completed(self):
        pdf = SimpleUploadedFile("doc.pdf", _make_scanned_pdf("APITEST"), content_type="application/pdf")
        response = self.client.post("/api/v1/pdf/ocr/", data={"file": pdf, "language": "eng"}, format="multipart")

        self.assertEqual(response.status_code, 201)
        body = response.json()["data"]
        job_id = body["job_id"]
        token = body["owner_token"]

        status_response = self.client.get(f"/api/v1/pdf/ocr/{job_id}/status/?token={token}")
        self.assertEqual(status_response.status_code, 200)
        status_data = status_response.json()["data"]
        self.assertEqual(status_data["status"], "completed")
        self.assertEqual(status_data["ocr_page_count"], 1)
        self.assertIsNotNone(status_data["download_url"])

        download_url = status_data["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "application/pdf")

        downloaded_bytes = b"".join(download.streaming_content)
        doc = fitz.open(stream=downloaded_bytes, filetype="pdf")
        extracted = doc[0].get_text().upper().replace(" ", "")
        doc.close()
        self.assertIn("APITEST", extracted)

        job = OcrJob.objects.get(id=job_id)
        job.output_file.delete(save=False)

    def test_invalid_language_is_rejected_at_submission(self):
        pdf = SimpleUploadedFile("doc.pdf", _make_text_pdf("Hello"), content_type="application/pdf")
        response = self.client.post("/api/v1/pdf/ocr/", data={"file": pdf, "language": "xyz"}, format="multipart")
        self.assertEqual(response.status_code, 400)

    def test_missing_file_is_rejected(self):
        response = self.client.post("/api/v1/pdf/ocr/", data={}, format="multipart")
        self.assertEqual(response.status_code, 400)

    def test_rejects_non_pdf_file(self):
        not_a_pdf = SimpleUploadedFile("evil.txt", b"hello", content_type="text/plain")
        response = self.client.post("/api/v1/pdf/ocr/", data={"file": not_a_pdf}, format="multipart")
        self.assertEqual(response.status_code, 400)


class OcrOwnershipTests(TestCase):
    """
    Access-control regression suite for the shared ownership layer, as
    exercised through OCR's status/download endpoints. Builds a completed
    job directly via the ORM (bypassing real OCR/Tesseract entirely) since
    these tests only care about access control, not OCR correctness - so
    they run everywhere, not just where Tesseract is installed.
    """

    def _create_completed_job(self):
        from apps.common.ownership import generate_owner_token

        job = OcrJob.objects.create(
            owner_token=generate_owner_token(),
            original_filename="doc.pdf",
            language="eng",
            status=OcrJob.Status.COMPLETED,
            page_count=1,
            ocr_page_count=1,
        )
        job.output_file.save("out.pdf", ContentFile(_make_text_pdf("Done")), save=True)
        return job

    def test_owner_can_poll_status_and_download(self):
        job = self._create_completed_job()

        status_response = self.client.get(f"/api/v1/pdf/ocr/{job.id}/status/?token={job.owner_token}")
        self.assertEqual(status_response.status_code, 200)
        self.assertIsNotNone(status_response.json()["data"]["download_url"])

        download_response = self.client.get(f"/api/v1/pdf/ocr/{job.id}/download/?token={job.owner_token}")
        self.assertEqual(download_response.status_code, 200)
        self.assertEqual(download_response["Content-Type"], "application/pdf")

        job.output_file.delete(save=False)

    def test_wrong_token_is_denied_on_status(self):
        job = self._create_completed_job()
        response = self.client.get(f"/api/v1/pdf/ocr/{job.id}/status/?token=wrong-token")
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")
        job.output_file.delete(save=False)

    def test_wrong_token_is_denied_on_download(self):
        job = self._create_completed_job()
        response = self.client.get(f"/api/v1/pdf/ocr/{job.id}/download/?token=wrong-token")
        self.assertEqual(response.status_code, 404)
        job.output_file.delete(save=False)

    def test_missing_token_is_denied(self):
        job = self._create_completed_job()
        response = self.client.get(f"/api/v1/pdf/ocr/{job.id}/status/")
        self.assertEqual(response.status_code, 404)
        job.output_file.delete(save=False)

    def test_nonexistent_job_id_returns_identical_response_shape(self):
        import uuid

        response = self.client.get(f"/api/v1/pdf/ocr/{uuid.uuid4()}/status/?token=whatever")
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

    def test_malformed_job_id_is_handled_safely_not_a_500(self):
        response = self.client.get("/api/v1/pdf/ocr/not-a-valid-uuid/status/?token=whatever")
        self.assertEqual(response.status_code, 404)

    def test_raw_media_path_does_not_serve_the_output_file(self):
        job = self._create_completed_job()
        response = self.client.get(f"/media/ocr/output/{job.id}.pdf")
        self.assertEqual(response.status_code, 404)
        job.output_file.delete(save=False)

    def test_download_not_ready_for_a_processing_job(self):
        from apps.common.ownership import generate_owner_token

        job = OcrJob.objects.create(
            owner_token=generate_owner_token(), status=OcrJob.Status.PROCESSING,
        )
        response = self.client.get(f"/api/v1/pdf/ocr/{job.id}/download/?token={job.owner_token}")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error_code"], "NOT_READY")
