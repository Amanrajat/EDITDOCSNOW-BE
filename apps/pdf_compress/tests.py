import fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from rest_framework import serializers

from apps.common.validation import validate_pdf_file

from .models import CompressJob
from .services import CompressError, CompressPDFService, validate_level


def _make_text_only_pdf(name, page_texts):
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page(width=400, height=500)
        page.insert_text(fitz.Point(40, 60), text, fontsize=14)
    data = doc.tobytes()
    doc.close()
    return SimpleUploadedFile(name, data, content_type="application/pdf")


def _noisy_photo_like_png(width=1400, height=1800):
    """Builds a real, high-entropy PNG (varied per-pixel colors, not a flat
    fill) so JPEG recompression has genuine content to compress - a
    single-color image would compress to almost nothing regardless of
    settings and wouldn't prove anything about the pipeline."""
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height))
    for x in range(0, width, 17):
        pix.set_rect(fitz.IRect(x, 0, x + 9, height), ((x * 7) % 255, (x * 13) % 255, (x * 31) % 255))
    for y in range(0, height, 23):
        pix.set_rect(fitz.IRect(0, y, width, y + 6), ((y * 11) % 255, (y * 5) % 255, (y * 19) % 255))
    return pix.tobytes("png")


def _make_image_heavy_pdf(name, page_count=1, page_texts=None):
    doc = fitz.open()
    png_bytes = _noisy_photo_like_png()
    for i in range(page_count):
        page = doc.new_page(width=612, height=792)
        page.insert_image(fitz.Rect(20, 20, 592, 500), stream=png_bytes)
        if page_texts and i < len(page_texts):
            page.insert_text(fitz.Point(40, 550), page_texts[i], fontsize=14)
    data = doc.tobytes()
    doc.close()
    return SimpleUploadedFile(name, data, content_type="application/pdf")


class ValidateLevelTests(TestCase):

    def test_accepts_all_four_levels(self):
        for level in ["high_quality", "recommended", "high_compression", "maximum_compression"]:
            validate_level(level)

    def test_rejects_unknown_level(self):
        with self.assertRaises(CompressError):
            validate_level("ultra_max")


class CompressPDFServiceTests(TestCase):

    def test_compresses_image_heavy_pdf(self):
        pdf = _make_image_heavy_pdf("photo.pdf")
        output_bytes, page_count, original_size, compressed_size = CompressPDFService.compress(
            pdf.read(), CompressJob.Level.MAXIMUM_COMPRESSION,
        )
        self.assertEqual(page_count, 1)
        self.assertLess(compressed_size, original_size)
        self.assertEqual(len(output_bytes), compressed_size)

        # Real output must still open and have the same page count.
        doc = fitz.open(stream=output_bytes, filetype="pdf")
        self.assertEqual(len(doc), 1)
        doc.close()

    def test_higher_compression_level_yields_smaller_output(self):
        pdf_bytes = _make_image_heavy_pdf("photo.pdf").read()

        recommended_bytes, _, _, recommended_size = CompressPDFService.compress(
            pdf_bytes, CompressJob.Level.RECOMMENDED,
        )
        max_bytes, _, _, max_size = CompressPDFService.compress(
            pdf_bytes, CompressJob.Level.MAXIMUM_COMPRESSION,
        )

        self.assertLess(max_size, recommended_size)

    def test_never_returns_a_larger_file_than_the_original(self):
        # A tiny, already-minimal text-only PDF has nothing worth
        # compressing - the service must fall back to the original rather
        # than emit something bigger.
        pdf = _make_text_only_pdf("doc.pdf", ["Hello"])
        output_bytes, _, original_size, compressed_size = CompressPDFService.compress(
            pdf.read(), CompressJob.Level.MAXIMUM_COMPRESSION,
        )
        self.assertLessEqual(compressed_size, original_size)
        self.assertLessEqual(len(output_bytes), original_size)

    def test_preserves_page_count_across_levels(self):
        pdf_bytes = _make_image_heavy_pdf("photo.pdf", page_count=4).read()
        output_bytes, page_count, _, _ = CompressPDFService.compress(pdf_bytes, CompressJob.Level.HIGH_COMPRESSION)
        self.assertEqual(page_count, 4)
        doc = fitz.open(stream=output_bytes, filetype="pdf")
        self.assertEqual(len(doc), 4)
        doc.close()

    def test_preserves_text_content(self):
        pdf = _make_image_heavy_pdf("photo.pdf", page_texts=["Keep this readable text"])
        output_bytes, _, _, _ = CompressPDFService.compress(pdf.read(), CompressJob.Level.HIGH_COMPRESSION)
        doc = fitz.open(stream=output_bytes, filetype="pdf")
        self.assertIn("Keep this readable text", doc[0].get_text())
        doc.close()

    def test_rejects_invalid_level(self):
        pdf = _make_text_only_pdf("doc.pdf", ["Hello"])
        with self.assertRaises(CompressError):
            CompressPDFService.compress(pdf.read(), "ultra_max")

    def test_run_creates_completed_job_with_correct_stats(self):
        pdf = _make_image_heavy_pdf("photo.pdf")
        job = CompressPDFService.run(user=None, uploaded_file=pdf, level=CompressJob.Level.MAXIMUM_COMPRESSION)

        self.assertEqual(job.status, CompressJob.Status.COMPLETED)
        self.assertEqual(job.page_count, 1)
        self.assertGreater(job.original_size, 0)
        self.assertLess(job.compressed_size, job.original_size)
        self.assertEqual(job.saved_size, job.original_size - job.compressed_size)
        self.assertGreater(job.reduction_percent, 0)
        self.assertTrue(job.output_file.name)

        job.output_file.delete(save=False)

    def test_run_deletes_job_and_raises_on_invalid_level(self):
        pdf = _make_text_only_pdf("doc.pdf", ["Hello"])
        with self.assertRaises(CompressError):
            CompressPDFService.run(user=None, uploaded_file=pdf, level="ultra_max")
        self.assertEqual(CompressJob.objects.count(), 0)


class CompressPDFAPITests(TestCase):

    def _post(self, pdf, level=None):
        data = {"file": pdf}
        if level is not None:
            data["level"] = level
        return self.client.post("/api/v1/pdf/compress/", data=data, format="multipart")

    def test_compress_end_to_end_with_real_stats(self):
        pdf = _make_image_heavy_pdf("photo.pdf")
        response = self._post(pdf, level="maximum_compression")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["success"])
        data = body["data"]
        self.assertEqual(data["page_count"], 1)
        self.assertEqual(data["level"], "maximum_compression")
        self.assertGreater(data["original_size"], 0)
        self.assertLess(data["compressed_size"], data["original_size"])
        self.assertEqual(data["saved_size"], data["original_size"] - data["compressed_size"])
        self.assertGreater(data["reduction_percent"], 0)

        download_url = data["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "application/pdf")

        downloaded_bytes = b"".join(download.streaming_content)
        self.assertEqual(len(downloaded_bytes), data["compressed_size"])
        doc = fitz.open(stream=downloaded_bytes, filetype="pdf")
        self.assertEqual(len(doc), 1)
        doc.close()

        job = CompressJob.objects.get(id=data["file_id"])
        job.output_file.delete(save=False)

    def test_defaults_to_recommended_level(self):
        pdf = _make_text_only_pdf("doc.pdf", ["Hello"])
        response = self._post(pdf)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["data"]["level"], "recommended")

        job = CompressJob.objects.get(id=response.json()["data"]["file_id"])
        job.output_file.delete(save=False)

    def test_invalid_level_is_rejected(self):
        pdf = _make_text_only_pdf("doc.pdf", ["Hello"])
        response = self._post(pdf, level="ultra_max")
        self.assertEqual(response.status_code, 400)

    def test_missing_file_is_rejected(self):
        response = self.client.post("/api/v1/pdf/compress/", data={}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("file", response.json()["errors"])

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
        pdf = _make_text_only_pdf("doc.pdf", ["Hello"])
        with self.assertRaises(serializers.ValidationError):
            validate_pdf_file(pdf, max_size=10)


class CompressJobOwnershipTests(TestCase):
    """
    Access-control regression suite for the shared ownership layer, as
    exercised through Compress PDF's download endpoint. Mirrors the
    equivalent ownership test classes across every other PDF job app -
    same shared apps.common.ownership/views layer.
    """

    def _create_job(self):
        pdf = _make_text_only_pdf("doc.pdf", ["Hello"])
        response = self.client.post(
            "/api/v1/pdf/compress/", data={"file": pdf}, format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["data"]

    def test_owner_can_download_with_correct_token(self):
        data = self._create_job()
        download_url = data["download_url"].replace("http://testserver", "")

        response = self.client.get(download_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")

        job = CompressJob.objects.get(id=data["file_id"])
        job.output_file.delete(save=False)

    def test_wrong_token_is_denied(self):
        data = self._create_job()
        job = CompressJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/api/v1/pdf/compress/{job.id}/download/?token=not-the-real-token")

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

        job.output_file.delete(save=False)

    def test_missing_token_is_denied(self):
        data = self._create_job()
        job = CompressJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/api/v1/pdf/compress/{job.id}/download/")

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

        job.output_file.delete(save=False)

    def test_nonexistent_job_id_returns_identical_response_shape_to_wrong_token(self):
        import uuid

        response = self.client.get(f"/api/v1/pdf/compress/{uuid.uuid4()}/download/?token=whatever")

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

    def test_malformed_job_id_is_handled_safely_not_a_500(self):
        response = self.client.get("/api/v1/pdf/compress/not-a-valid-uuid/download/?token=whatever")
        self.assertEqual(response.status_code, 404)

    def test_raw_media_path_does_not_serve_the_output_file(self):
        data = self._create_job()
        job = CompressJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/media/compress/output/{job.id}.pdf")

        self.assertEqual(response.status_code, 404)

        job.output_file.delete(save=False)
