from django.core.exceptions import ValidationError
from django.http import FileResponse
from django.urls import reverse
from rest_framework.views import APIView

from apps.common.ownership import is_owner
from apps.common.responses import error_response, success_response

from .models import BatchFileJob, BatchJob
from .serializers import BatchCompressRequestSerializer
from .services import BatchError, BatchService
from .tasks import process_batch_file


def _file_job_dict(file_job):
    data = {
        "id": str(file_job.id),
        "order": file_job.order,
        "filename": file_job.original_filename,
        "status": file_job.status,
    }
    if file_job.status == BatchFileJob.Status.FAILED:
        data["error"] = file_job.error_message
    if file_job.status == BatchFileJob.Status.COMPLETED:
        data.update({
            "page_count": file_job.page_count,
            "original_size": file_job.original_size,
            "compressed_size": file_job.compressed_size,
            "saved_size": max(0, file_job.original_size - file_job.compressed_size),
        })
    return data


class BatchCompressView(APIView):
    """
    POST /api/v1/pdf/batch/compress/

    multipart/form-data:
        files: one or more PDFs, repeated fields (e.g. files=a.pdf&files=b.pdf)
        level: optional, one of high_quality/recommended/high_compression/
               maximum_compression, default recommended

    Processes each file asynchronously via Celery - poll
    GET /api/v1/pdf/batch/<batch_id>/status/ for progress, then
    GET /api/v1/pdf/batch/<batch_id>/download/ for the ZIP once done.
    A bad individual file is marked failed on its own entry rather than
    rejecting the whole batch.
    """

    def post(self, request):
        serializer = BatchCompressRequestSerializer(data=request.data)

        if not serializer.is_valid():
            return error_response(
                "Invalid request.",
                error_code="VALIDATION_ERROR",
                status_code=400,
                errors=serializer.errors,
            )

        data = serializer.validated_data
        user = request.user if request.user.is_authenticated else None

        try:
            batch, file_jobs = BatchService.create_batch(
                user=user,
                uploaded_files=data["files"],
                operation=BatchJob.Operation.COMPRESS,
                options={"level": data["level"]},
            )
        except BatchError as exc:
            return error_response(str(exc), error_code="VALIDATION_ERROR", status_code=400)

        for file_job in file_jobs:
            if file_job.status == BatchFileJob.Status.QUEUED:
                process_batch_file.delay(str(file_job.id))

        # If every file failed synchronous validation, there's nothing
        # left to process - finalize immediately instead of waiting on
        # Celery tasks that were never dispatched.
        if not any(f.status == BatchFileJob.Status.QUEUED for f in file_jobs):
            BatchService.finalize_if_done(batch.id)
            batch.refresh_from_db()

        status_path = reverse("pdf_batch:batch-status", args=[batch.id])
        status_url = request.build_absolute_uri(f"{status_path}?token={batch.owner_token}")

        return success_response(
            "Batch submitted",
            data={
                "batch_id": str(batch.id),
                "owner_token": batch.owner_token,
                "status_url": status_url,
                "total_files": batch.total_files,
                "status": batch.status,
                "files": [_file_job_dict(f) for f in file_jobs],
            },
            status_code=201,
        )


class BatchStatusView(APIView):
    """
    GET /api/v1/pdf/batch/<batch_id>/status/?token=<owner_token>

    Reusable polling endpoint: overall batch status plus per-file status,
    matching the queued/processing/completed/failed states the frontend's
    JobStatus component expects.
    """

    def get(self, request, batch_id):
        try:
            batch = BatchJob.objects.prefetch_related("files").get(id=batch_id)
        except (BatchJob.DoesNotExist, ValueError, ValidationError):
            return error_response(
                "No such batch, or you don't have access to it.",
                error_code="NOT_FOUND", status_code=404,
            )

        if not is_owner(request, batch):
            return error_response(
                "No such batch, or you don't have access to it.",
                error_code="NOT_FOUND", status_code=404,
            )

        download_url = None
        if batch.status in (BatchJob.Status.COMPLETED, BatchJob.Status.PARTIAL) and batch.output_zip:
            download_path = reverse("pdf_batch:batch-download", args=[batch.id])
            download_url = request.build_absolute_uri(f"{download_path}?token={batch.owner_token}")

        files = sorted(batch.files.all(), key=lambda f: f.order)

        return success_response(
            "Batch status",
            data={
                "batch_id": str(batch.id),
                "status": batch.status,
                "total_files": batch.total_files,
                "completed_count": sum(1 for f in files if f.status == BatchFileJob.Status.COMPLETED),
                "failed_count": sum(1 for f in files if f.status == BatchFileJob.Status.FAILED),
                "download_url": download_url,
                "files": [_file_job_dict(f) for f in files],
            },
        )


class BatchDownloadView(APIView):
    """
    GET /api/v1/pdf/batch/<batch_id>/download/?token=<owner_token>

    Downloads the ZIP of every successfully processed file in the batch.
    Available once the batch is "completed" or "partial" (some files
    failed but at least one succeeded) - not a straight subclass of
    OwnedJobDownloadView since "ready" here is two statuses, not one.
    """

    def get(self, request, batch_id):
        try:
            batch = BatchJob.objects.get(id=batch_id)
        except (BatchJob.DoesNotExist, ValueError, ValidationError):
            return error_response(
                "No such batch, or you don't have access to it.",
                error_code="NOT_FOUND", status_code=404,
            )

        if not is_owner(request, batch):
            return error_response(
                "No such batch, or you don't have access to it.",
                error_code="NOT_FOUND", status_code=404,
            )

        ready = batch.status in (BatchJob.Status.COMPLETED, BatchJob.Status.PARTIAL) and batch.output_zip
        if not ready:
            return error_response(
                "This batch is not ready for download.",
                error_code="NOT_READY", status_code=404,
            )

        as_attachment = request.query_params.get("disposition") != "inline"
        return FileResponse(
            batch.output_zip.open("rb"),
            as_attachment=as_attachment,
            filename="batch_compressed.zip",
            content_type="application/zip",
        )
