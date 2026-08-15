import zipfile
from io import BytesIO

import fitz
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from .models import BatchFileJob, BatchJob
from .services import BatchError, BatchService

# CELERY_TASK_ALWAYS_EAGER makes .delay() run the task synchronously,
# in-process, with no broker/worker needed - the standard way to test
# Celery-backed code without spinning up Redis.
EAGER = override_settings(CELERY_TASK_ALWAYS_EAGER=True, CELERY_TASK_EAGER_PROPAGATES=True)


def _make_pdf_file(name, page_texts):
    doc = fitz.open()
    for text in page_texts:
        page = doc.new_page(width=300, height=200)
        page.insert_text(fitz.Point(20, 30), text, fontsize=14)
    data = doc.tobytes()
    doc.close()
    return SimpleUploadedFile(name, data, content_type="application/pdf")


def _make_image_pdf(name):
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 600, 400))
    for x in range(0, 600, 20):
        pix.set_rect(fitz.IRect(x, 0, x + 10, 400), (x % 255, (x * 3) % 255, (x * 7) % 255))
    page.insert_image(fitz.Rect(0, 0, 300, 200), stream=pix.tobytes("png"))
    data = doc.tobytes()
    doc.close()
    return SimpleUploadedFile(name, data, content_type="application/pdf")


class BatchServiceTests(TestCase):

    def test_create_batch_queues_valid_files(self):
        files = [_make_pdf_file("a.pdf", ["A"]), _make_pdf_file("b.pdf", ["B"])]
        batch, file_jobs = BatchService.create_batch(
            user=None, uploaded_files=files, operation=BatchJob.Operation.COMPRESS,
            options={"level": "recommended"},
        )
        self.assertEqual(batch.total_files, 2)
        self.assertEqual(batch.status, BatchJob.Status.PROCESSING)
        self.assertEqual([f.status for f in file_jobs], [BatchFileJob.Status.QUEUED, BatchFileJob.Status.QUEUED])
        for f in file_jobs:
            self.assertTrue(f.source_file.name)

    def test_create_batch_isolates_a_bad_file_without_rejecting_the_rest(self):
        good = _make_pdf_file("good.pdf", ["Good"])
        bad = SimpleUploadedFile("bad.pdf", b"not a real pdf", content_type="application/pdf")
        batch, file_jobs = BatchService.create_batch(
            user=None, uploaded_files=[good, bad], operation=BatchJob.Operation.COMPRESS,
            options={"level": "recommended"},
        )
        statuses = {f.original_filename: f.status for f in file_jobs}
        self.assertEqual(statuses["good.pdf"], BatchFileJob.Status.QUEUED)
        self.assertEqual(statuses["bad.pdf"], BatchFileJob.Status.FAILED)

        bad_job = next(f for f in file_jobs if f.original_filename == "bad.pdf")
        self.assertTrue(bad_job.error_message)
        self.assertFalse(bad_job.source_file)

    def test_create_batch_rejects_empty_file_list(self):
        with self.assertRaises(BatchError):
            BatchService.create_batch(
                user=None, uploaded_files=[], operation=BatchJob.Operation.COMPRESS, options={},
            )

    def test_create_batch_rejects_too_many_files(self):
        files = [_make_pdf_file(f"{i}.pdf", [str(i)]) for i in range(31)]
        with self.assertRaises(BatchError):
            BatchService.create_batch(
                user=None, uploaded_files=files, operation=BatchJob.Operation.COMPRESS, options={},
            )

    def test_process_file_completes_and_cleans_up_source(self):
        batch, file_jobs = BatchService.create_batch(
            user=None, uploaded_files=[_make_image_pdf("photo.pdf")],
            operation=BatchJob.Operation.COMPRESS, options={"level": "maximum_compression"},
        )
        file_job = file_jobs[0]
        BatchService.process_file(str(file_job.id))

        file_job.refresh_from_db()
        self.assertEqual(file_job.status, BatchFileJob.Status.COMPLETED)
        self.assertTrue(file_job.output_file.name)
        self.assertFalse(file_job.source_file)  # cleaned up
        self.assertGreater(file_job.original_size, 0)

        file_job.output_file.delete(save=False)

    def test_finalize_marks_completed_when_all_files_succeed(self):
        batch, file_jobs = BatchService.create_batch(
            user=None, uploaded_files=[_make_pdf_file("a.pdf", ["A"]), _make_pdf_file("b.pdf", ["B"])],
            operation=BatchJob.Operation.COMPRESS, options={"level": "recommended"},
        )
        for f in file_jobs:
            BatchService.process_file(str(f.id))

        batch.refresh_from_db()
        self.assertEqual(batch.status, BatchJob.Status.COMPLETED)
        self.assertTrue(batch.output_zip.name)

        with batch.output_zip.open("rb") as fh:
            zf = zipfile.ZipFile(BytesIO(fh.read()))
            self.assertEqual(sorted(zf.namelist()), ["a.pdf", "b.pdf"])

        batch.output_zip.delete(save=False)
        for f in file_jobs:
            if f.output_file:
                f.output_file.delete(save=False)

    def test_finalize_marks_partial_when_some_files_fail(self):
        good = _make_pdf_file("good.pdf", ["Good"])
        bad = SimpleUploadedFile("bad.pdf", b"not a real pdf", content_type="application/pdf")
        batch, file_jobs = BatchService.create_batch(
            user=None, uploaded_files=[good, bad], operation=BatchJob.Operation.COMPRESS,
            options={"level": "recommended"},
        )
        good_job = next(f for f in file_jobs if f.original_filename == "good.pdf")
        BatchService.process_file(str(good_job.id))  # the bad one never got queued, so nothing to process for it
        BatchService.finalize_if_done(batch.id)

        batch.refresh_from_db()
        self.assertEqual(batch.status, BatchJob.Status.PARTIAL)
        self.assertTrue(batch.output_zip.name)

        with batch.output_zip.open("rb") as fh:
            zf = zipfile.ZipFile(BytesIO(fh.read()))
            self.assertEqual(zf.namelist(), ["good.pdf"])

        batch.output_zip.delete(save=False)
        good_job.refresh_from_db()
        if good_job.output_file:
            good_job.output_file.delete(save=False)

    def test_finalize_marks_failed_when_every_file_fails(self):
        bad = SimpleUploadedFile("bad.pdf", b"not a real pdf", content_type="application/pdf")
        batch, file_jobs = BatchService.create_batch(
            user=None, uploaded_files=[bad], operation=BatchJob.Operation.COMPRESS, options={},
        )
        BatchService.finalize_if_done(batch.id)
        batch.refresh_from_db()
        self.assertEqual(batch.status, BatchJob.Status.FAILED)
        self.assertFalse(batch.output_zip)

    def test_duplicate_filenames_get_disambiguated_in_the_zip(self):
        batch, file_jobs = BatchService.create_batch(
            user=None,
            uploaded_files=[_make_pdf_file("doc.pdf", ["A"]), _make_pdf_file("doc.pdf", ["B"])],
            operation=BatchJob.Operation.COMPRESS, options={"level": "recommended"},
        )
        for f in file_jobs:
            BatchService.process_file(str(f.id))

        batch.refresh_from_db()
        with batch.output_zip.open("rb") as fh:
            zf = zipfile.ZipFile(BytesIO(fh.read()))
            self.assertEqual(sorted(zf.namelist()), ["doc.pdf", "doc.pdf_1"])

        batch.output_zip.delete(save=False)
        for f in file_jobs:
            f.refresh_from_db()
            if f.output_file:
                f.output_file.delete(save=False)


@EAGER
class BatchCompressAPITests(TestCase):

    def test_submit_and_poll_status_end_to_end(self):
        files = [_make_pdf_file("a.pdf", ["A"]), _make_pdf_file("b.pdf", ["B"])]
        response = self.client.post(
            "/api/v1/pdf/batch/compress/",
            data={"files": files, "level": "recommended"},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()["data"]
        self.assertEqual(body["total_files"], 2)
        batch_id = body["batch_id"]
        token = body["owner_token"]

        status_response = self.client.get(f"/api/v1/pdf/batch/{batch_id}/status/?token={token}")
        self.assertEqual(status_response.status_code, 200)
        status_body = status_response.json()["data"]
        self.assertEqual(status_body["status"], "completed")
        self.assertEqual(status_body["completed_count"], 2)
        self.assertEqual(status_body["failed_count"], 0)
        self.assertIsNotNone(status_body["download_url"])

        download_url = status_body["download_url"].replace("http://testserver", "")
        download = self.client.get(download_url)
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "application/zip")

        zf = zipfile.ZipFile(BytesIO(b"".join(download.streaming_content)))
        self.assertEqual(sorted(zf.namelist()), ["a.pdf", "b.pdf"])

        batch = BatchJob.objects.get(id=batch_id)
        batch.output_zip.delete(save=False)
        for f in batch.files.all():
            if f.output_file:
                f.output_file.delete(save=False)

    def test_partial_batch_still_produces_a_downloadable_zip(self):
        good = _make_pdf_file("good.pdf", ["Good"])
        bad = SimpleUploadedFile("bad.pdf", b"not a real pdf", content_type="application/pdf")
        response = self.client.post(
            "/api/v1/pdf/batch/compress/", data={"files": [good, bad]}, format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        body = response.json()["data"]

        status_response = self.client.get(
            f"/api/v1/pdf/batch/{body['batch_id']}/status/?token={body['owner_token']}"
        )
        status_body = status_response.json()["data"]
        self.assertEqual(status_body["status"], "partial")
        self.assertEqual(status_body["completed_count"], 1)
        self.assertEqual(status_body["failed_count"], 1)
        failed_entry = next(f for f in status_body["files"] if f["filename"] == "bad.pdf")
        self.assertEqual(failed_entry["status"], "failed")
        self.assertTrue(failed_entry["error"])

        batch = BatchJob.objects.get(id=body["batch_id"])
        batch.output_zip.delete(save=False)
        for f in batch.files.all():
            if f.output_file:
                f.output_file.delete(save=False)

    def test_missing_files_is_rejected(self):
        response = self.client.post("/api/v1/pdf/batch/compress/", data={}, format="multipart")
        self.assertEqual(response.status_code, 400)

    def test_too_many_files_is_rejected(self):
        files = [_make_pdf_file(f"{i}.pdf", [str(i)]) for i in range(31)]
        response = self.client.post(
            "/api/v1/pdf/batch/compress/", data={"files": files}, format="multipart",
        )
        self.assertEqual(response.status_code, 400)


class BatchOwnershipTests(TestCase):
    """
    Access-control regression suite for the shared ownership layer, as
    exercised through Batch's status/download endpoints. BatchStatusView/
    BatchDownloadView don't subclass OwnedJobDownloadView (their "ready"
    condition differs), but reuse the same apps.common.ownership.is_owner
    check and error shape, verified here.
    """

    @EAGER
    def _create_batch(self):
        response = self.client.post(
            "/api/v1/pdf/batch/compress/",
            data={"files": [_make_pdf_file("a.pdf", ["A"])]},
            format="multipart",
        )
        self.assertEqual(response.status_code, 201)
        return response.json()["data"]

    def test_wrong_token_is_denied_on_status(self):
        data = self._create_batch()
        response = self.client.get(f"/api/v1/pdf/batch/{data['batch_id']}/status/?token=wrong-token")
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")

    def test_wrong_token_is_denied_on_download(self):
        data = self._create_batch()
        response = self.client.get(f"/api/v1/pdf/batch/{data['batch_id']}/download/?token=wrong-token")
        self.assertEqual(response.status_code, 404)

    def test_owner_can_poll_status_and_download(self):
        data = self._create_batch()
        status_response = self.client.get(
            f"/api/v1/pdf/batch/{data['batch_id']}/status/?token={data['owner_token']}"
        )
        self.assertEqual(status_response.status_code, 200)

        batch = BatchJob.objects.get(id=data["batch_id"])
        if batch.output_zip:
            batch.output_zip.delete(save=False)
        for f in batch.files.all():
            if f.output_file:
                f.output_file.delete(save=False)

    def test_malformed_batch_id_is_handled_safely_not_a_500(self):
        response = self.client.get("/api/v1/pdf/batch/not-a-valid-uuid/status/?token=whatever")
        self.assertEqual(response.status_code, 404)

    def test_nonexistent_batch_id_returns_identical_response_shape(self):
        import uuid

        response = self.client.get(f"/api/v1/pdf/batch/{uuid.uuid4()}/status/?token=whatever")
        self.assertEqual(response.status_code, 404)
        body = response.json()
        self.assertFalse(body["success"])
        self.assertEqual(body["error_code"], "NOT_FOUND")
