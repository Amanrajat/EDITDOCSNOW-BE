from django.urls import reverse
from rest_framework.views import APIView

from apps.common.responses import error_response, success_response
from apps.common.views import OwnedJobDownloadView

from .models import RotateJob
from .serializers import RotatePDFRequestSerializer
from .services import RotateError, RotatePDFService


class RotatePDFView(APIView):
    """
    POST /api/v1/pdf/rotate/

    multipart/form-data:
        file:    a single PDF
        pages:   optional, 1-based page numbers to rotate, repeated fields
                 (e.g. pages=1&pages=3) - omit to rotate every page
        degrees: required, non-zero multiple of 90 (e.g. 90, -90, 180, 270)
    """

    def post(self, request):
        serializer = RotatePDFRequestSerializer(data=request.data)

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
            job = RotatePDFService.run(
                user=user,
                uploaded_file=data["file"],
                pages=data.get("pages"),
                degrees=data["degrees"],
            )
        except RotateError as exc:
            return error_response(
                str(exc), error_code="VALIDATION_ERROR", status_code=400,
            )

        if job.status == RotateJob.Status.FAILED:
            return error_response(
                "Unable to rotate PDF.",
                error_code="PDF_PROCESSING_FAILED",
                status_code=500,
            )

        download_path = reverse("pdf_rotate:rotate-download", args=[job.id])
        download_url = request.build_absolute_uri(
            f"{download_path}?token={job.owner_token}"
        )

        return success_response(
            "PDF rotated successfully",
            data={
                "file_id": str(job.id),
                "owner_token": job.owner_token,
                "download_url": download_url,
                "filename": "rotated.pdf",
                "page_count": job.page_count,
                "rotated_pages": job.rotated_pages,
                "degrees": job.degrees,
            },
            status_code=201,
        )


class RotateJobDownloadView(OwnedJobDownloadView):
    """
    GET /api/v1/pdf/rotate/<job_id>/download/?token=<owner_token>

    See apps.common.ownership for the access-control policy this enforces.
    """

    model = RotateJob

    def get_filename(self, job):
        return "rotated.pdf"
