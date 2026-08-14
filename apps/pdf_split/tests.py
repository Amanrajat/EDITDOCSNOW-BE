import fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from .models import SplitJob
from .services import SplitError, SplitPDFService, chunk_every_n, parse_ranges


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


class ParseRangesTests(TestCase):

    def test_parses_mixed_single_and_range_tokens(self):
        self.assertEqual(parse_ranges("1-3,5,7-9", total_pages=10), [(1, 3), (5, 5), (7, 9)])

    def test_accepts_newline_separators(self):
        self.assertEqual(parse_ranges("1-2\n3-4", total_pages=10), [(1, 2), (3, 4)])

    def test_allows_overlapping_ranges_without_deduplication(self):
        # Documented rule: overlaps are NOT merged/rejected - each token is
        # independent and may repeat pages across output files.
        self.assertEqual(parse_ranges("1-3,2-4", total_pages=10), [(1, 3), (2, 4)])

    def test_rejects_reversed_range(self):
        with self.assertRaises(SplitError):
            parse_ranges("5-2", total_pages=10)

    def test_rejects_out_of_bounds_page(self):
        with self.assertRaises(SplitError):
            parse_ranges("1-11", total_pages=10)

    def test_rejects_zero_or_negative_page(self):
        with self.assertRaises(SplitError):
            parse_ranges("0-3", total_pages=10)

    def test_rejects_malformed_token(self):
        with self.assertRaises(SplitError):
            parse_ranges("abc", total_pages=10)

    def test_rejects_empty_input(self):
        with self.assertRaises(SplitError):
            parse_ranges("", total_pages=10)


class ChunkEveryNTests(TestCase):

    def test_even_division(self):
        self.assertEqual(chunk_every_n(10, 5), [(1, 5), (6, 10)])

    def test_uneven_division_shorter_last_chunk(self):
        self.assertEqual(chunk_every_n(12, 5), [(1, 5), (6, 10), (11, 12)])

    def test_n_larger_than_total_pages_yields_single_chunk(self):
        self.assertEqual(chunk_every_n(3, 100), [(1, 3)])

    def test_n_of_one_yields_one_chunk_per_page(self):
        self.assertEqual(chunk_every_n(3, 1), [(1, 1), (2, 2), (3, 3)])

    def test_rejects_n_below_one(self):
        with self.assertRaises(SplitError):
            chunk_every_n(10, 0)


class SplitPDFServiceTests(TestCase):

    def test_all_pages_mode_produces_one_file_per_page_in_order(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        outputs, total_pages = SplitPDFService.split(pdf.read(), SplitJob.Mode.ALL_PAGES)

        self.assertEqual(total_pages, 3)
        self.assertEqual([name for name, _ in outputs], ["page_1.pdf", "page_2.pdf", "page_3.pdf"])
        for (_, data), expected in zip(outputs, ["P1", "P2", "P3"]):
            self.assertEqual(_page_texts(data), [expected])

    def test_ranges_mode_produces_one_file_per_range(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3", "P4", "P5"])
        outputs, total_pages = SplitPDFService.split(
            pdf.read(), SplitJob.Mode.RANGES, ranges_text="1-2,3-5",
        )

        self.assertEqual(total_pages, 5)
        self.assertEqual([name for name, _ in outputs], ["pages_1-2.pdf", "pages_3-5.pdf"])
        self.assertEqual(_page_texts(outputs[0][1]), ["P1", "P2"])
        self.assertEqual(_page_texts(outputs[1][1]), ["P3", "P4", "P5"])

    def test_ranges_mode_single_page_range_uses_page_filename(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        outputs, _ = SplitPDFService.split(pdf.read(), SplitJob.Mode.RANGES, ranges_text="2")
        self.assertEqual(outputs[0][0], "page_2.pdf")
        self.assertEqual(_page_texts(outputs[0][1]), ["P2"])

    def test_every_n_mode_chunks_pages(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3", "P4", "P5"])
        outputs, total_pages = SplitPDFService.split(pdf.read(), SplitJob.Mode.EVERY_N, n=2)

        self.assertEqual(total_pages, 5)
        self.assertEqual(
            [name for name, _ in outputs],
            ["pages_1-2.pdf", "pages_3-4.pdf", "page_5.pdf"],
        )

    def test_extract_mode_produces_single_file_preserving_given_order(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3", "P4"])
        outputs, total_pages = SplitPDFService.split(
            pdf.read(), SplitJob.Mode.EXTRACT, pages=[3, 1, 1],
        )

        self.assertEqual(total_pages, 4)
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0][0], "extracted_pages.pdf")
        self.assertEqual(_page_texts(outputs[0][1]), ["P3", "P1", "P1"])

    def test_extract_mode_rejects_out_of_range_page(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        with self.assertRaises(SplitError):
            SplitPDFService.split(pdf.read(), SplitJob.Mode.EXTRACT, pages=[5])

    def test_run_all_pages_creates_completed_zip_job(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        job = SplitPDFService.run(user=None, uploaded_file=pdf, mode=SplitJob.Mode.ALL_PAGES)

        self.assertEqual(job.status, SplitJob.Status.COMPLETED)
        self.assertTrue(job.is_zip)
        self.assertEqual(job.output_count, 3)
        self.assertEqual(job.source_pages, 3)
        self.assertTrue(job.output_file.name.endswith(".zip"))

        job.output_file.delete(save=False)

    def test_run_extract_single_output_is_not_zipped(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        job = SplitPDFService.run(
            user=None, uploaded_file=pdf, mode=SplitJob.Mode.EXTRACT, pages=[2],
        )

        self.assertEqual(job.status, SplitJob.Status.COMPLETED)
        self.assertFalse(job.is_zip)
        self.assertEqual(job.output_count, 1)
        self.assertTrue(job.output_file.name.endswith(".pdf"))

        job.output_file.delete(save=False)

    def test_run_deletes_job_and_raises_on_split_error(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        with self.assertRaises(SplitError):
            SplitPDFService.run(
                user=None, uploaded_file=pdf, mode=SplitJob.Mode.RANGES,
                ranges_text="1-99",
            )
        self.assertEqual(SplitJob.objects.count(), 0)


class SplitPDFAPITests(TestCase):

    def _post(self, pdf, **extra):
        data = {"file": pdf, **extra}
        return self.client.post("/api/v1/pdf/split/", data=data, format="multipart")

    def test_all_pages_via_api_returns_zip(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self._post(pdf, mode="all_pages")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["success"])
        self.assertTrue(body["data"]["is_zip"])
        self.assertEqual(body["data"]["output_count"], 2)
        self.assertEqual(body["data"]["source_pages"], 2)

        download = self.client.get(body["data"]["download_url"].replace("http://testserver", ""))
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "application/zip")

        job = SplitJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)

    def test_extract_via_api_returns_single_pdf(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2", "P3"])
        response = self._post(pdf, mode="extract", pages=[1, 3])

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body["data"]["is_zip"])

        download = self.client.get(body["data"]["download_url"].replace("http://testserver", ""))
        downloaded_bytes = b"".join(download.streaming_content)
        self.assertEqual(_page_texts(downloaded_bytes), ["P1", "P3"])

        job = SplitJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)

    def test_ranges_mode_requires_ranges_field(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self._post(pdf, mode="ranges")

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertIn("ranges", body["errors"])

    def test_ranges_out_of_bounds_returns_400(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self._post(pdf, mode="ranges", ranges="1-5")

        self.assertEqual(response.status_code, 400)
        self.assertIn("ranges", response.json()["errors"])

    def test_every_n_requires_n_field(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self._post(pdf, mode="every_n")

        self.assertEqual(response.status_code, 400)
        self.assertIn("n", response.json()["errors"])

    def test_extract_requires_pages_field(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self._post(pdf, mode="extract")

        self.assertEqual(response.status_code, 400)
        self.assertIn("pages", response.json()["errors"])

    def test_extract_out_of_bounds_page_returns_400(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self._post(pdf, mode="extract", pages=[9])

        self.assertEqual(response.status_code, 400)
        self.assertIn("pages", response.json()["errors"])

    def test_rejects_non_pdf_file(self):
        not_a_pdf = SimpleUploadedFile("evil.txt", b"hello", content_type="text/plain")
        response = self._post(not_a_pdf, mode="all_pages")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_rejects_invalid_mode(self):
        pdf = _make_pdf_file("doc.pdf", ["P1"])
        response = self._post(pdf, mode="not_a_real_mode")

        self.assertEqual(response.status_code, 400)
        self.assertIn("mode", response.json()["errors"])

    def test_single_page_pdf_all_pages_mode(self):
        """Edge case: a 1-page document must still split cleanly (1 output, no zip)."""
        pdf = _make_pdf_file("doc.pdf", ["Only page"])
        response = self._post(pdf, mode="all_pages")

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body["data"]["is_zip"])
        self.assertEqual(body["data"]["output_count"], 1)

        job = SplitJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)

    def test_every_n_larger_than_total_pages_yields_single_file(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self._post(pdf, mode="every_n", n=100)

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertFalse(body["data"]["is_zip"])
        self.assertEqual(body["data"]["output_count"], 1)

        job = SplitJob.objects.get(id=body["data"]["file_id"])
        job.output_file.delete(save=False)


class SplitJobOwnershipTests(TestCase):
    """
    Access-control regression suite for the shared ownership layer, as
    exercised through Split PDF's download endpoint. Mirrors
    apps.pdf_merge.tests.MergeJobOwnershipTests.
    """

    def _create_job(self):
        pdf = _make_pdf_file("doc.pdf", ["P1", "P2"])
        response = self.client.post(
            "/api/v1/pdf/split/", data={"file": pdf, "mode": "all_pages"}, format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["data"]

    def test_owner_can_download_with_correct_token(self):
        data = self._create_job()
        download_url = data["download_url"].replace("http://testserver", "")

        response = self.client.get(download_url)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/zip")

        job = SplitJob.objects.get(id=data["file_id"])
        job.output_file.delete(save=False)

    def test_wrong_token_is_denied(self):
        data = self._create_job()
        job = SplitJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/api/v1/pdf/split/{job.id}/download/?token=not-the-real-token")

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

        job.output_file.delete(save=False)

    def test_missing_token_is_denied(self):
        data = self._create_job()
        job = SplitJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/api/v1/pdf/split/{job.id}/download/")

        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

        job.output_file.delete(save=False)

    def test_nonexistent_job_id_returns_identical_response_shape_to_wrong_token(self):
        import uuid

        response = self.client.get(f"/api/v1/pdf/split/{uuid.uuid4()}/download/?token=whatever")

        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

    def test_malformed_job_id_is_handled_safely_not_a_500(self):
        response = self.client.get("/api/v1/pdf/split/not-a-valid-uuid/download/?token=whatever")
        self.assertEqual(response.status_code, 404)

    def test_raw_media_path_no_longer_serves_the_output_file(self):
        data = self._create_job()
        job = SplitJob.objects.get(id=data["file_id"])

        response = self.client.get(f"/media/splits/output/{job.id}.zip")

        self.assertEqual(response.status_code, 404)

        job.output_file.delete(save=False)
