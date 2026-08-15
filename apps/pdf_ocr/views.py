from django.core.exceptions import ValidationError
from django.http import FileResponse
from django.urls import reverse
from rest_framework.views import APIView

from apps.common.ownership import is_owner
from apps.common.responses import error_response, success_response

from .models import OcrJob
from .serializers import OcrRequestSerializer
from .services import create_job
from .tasks import run_ocr_job


class OcrSubmitView(APIView):
    """
    POST /api/v1/pdf/ocr/

    multipart/form-data:
        file:     a single PDF
        language: optional, one or more Tesseract language codes joined
                  with "+" (e.g. "eng" or "eng+fra"), default "eng"

    Processes asynchronously via Celery - poll
    GET /api/v1/pdf/ocr/<job_id>/status/ for progress, then
    GET /api/v1/pdf/ocr/<job_id>/download/ for the searchable PDF once done.
    """

    def post(self, request):
        serializer = OcrRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "Invalid request.", error_code="VALIDATION_ERROR", status_code=400,
                errors=serializer.errors,
            )

        data = serializer.validated_data
        user = request.user if request.user.is_authenticated else None
        job = create_job(user=user, uploaded_file=data["file"], language=data["language"])
        run_ocr_job.delay(str(job.id))

        status_path = reverse("pdf_ocr:ocr-status", args=[job.id])
        status_url = request.build_absolute_uri(f"{status_path}?token={job.owner_token}")

        return success_response(
            "OCR job submitted",
            data={
                "job_id": str(job.id),
                "owner_token": job.owner_token,
                "status_url": status_url,
                "status": job.status,
                "language": job.language,
            },
            status_code=201,
        )


class OcrStatusView(APIView):
    """
    GET /api/v1/pdf/ocr/<job_id>/status/?token=<owner_token>

    Reusable polling endpoint (same shape/conventions as Batch
    Processing's status endpoint) - queued/processing/completed/failed.
    """

    def get(self, request, job_id):
        try:
            job = OcrJob.objects.get(id=job_id)
        except (OcrJob.DoesNotExist, ValueError, ValidationError):
            return error_response(
                "No such job, or you don't have access to it.",
                error_code="NOT_FOUND", status_code=404,
            )

        if not is_owner(request, job):
            return error_response(
                "No such job, or you don't have access to it.",
                error_code="NOT_FOUND", status_code=404,
            )

        download_url = None
        if job.status == OcrJob.Status.COMPLETED and job.output_file:
            download_path = reverse("pdf_ocr:ocr-download", args=[job.id])
            download_url = request.build_absolute_uri(f"{download_path}?token={job.owner_token}")

        return success_response(
            "OCR job status",
            data={
                "job_id": str(job.id),
                "status": job.status,
                "language": job.language,
                "page_count": job.page_count,
                "ocr_page_count": job.ocr_page_count,
                "error": job.error_message if job.status == OcrJob.Status.FAILED else None,
                "download_url": download_url,
            },
        )


class OcrDownloadView(APIView):
    """GET /api/v1/pdf/ocr/<job_id>/download/?token=<owner_token>"""

    def get(self, request, job_id):
        try:
            job = OcrJob.objects.get(id=job_id)
        except (OcrJob.DoesNotExist, ValueError, ValidationError):
            return error_response(
                "No such job, or you don't have access to it.",
                error_code="NOT_FOUND", status_code=404,
            )

        if not is_owner(request, job):
            return error_response(
                "No such job, or you don't have access to it.",
                error_code="NOT_FOUND", status_code=404,
            )

        if job.status != OcrJob.Status.COMPLETED or not job.output_file:
            return error_response(
                "This file is not ready for download.",
                error_code="NOT_READY", status_code=404,
            )

        as_attachment = request.query_params.get("disposition") != "inline"
        return FileResponse(
            job.output_file.open("rb"),
            as_attachment=as_attachment,
            filename="searchable.pdf",
            content_type="application/pdf",
        )
