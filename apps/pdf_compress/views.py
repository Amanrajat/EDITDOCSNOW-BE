from django.urls import reverse
from rest_framework.views import APIView

from apps.common.responses import error_response, success_response
from apps.common.views import OwnedJobDownloadView

from .models import CompressJob
from .serializers import CompressPDFRequestSerializer
from .services import CompressError, CompressPDFService


class CompressPDFView(APIView):
    """
    POST /api/v1/pdf/compress/

    multipart/form-data:
        file:  a single PDF
        level: optional, one of high_quality/recommended/high_compression/
               maximum_compression, default recommended
    """

    def post(self, request):
        serializer = CompressPDFRequestSerializer(data=request.data)

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
            job = CompressPDFService.run(
                user=user,
                uploaded_file=data["file"],
                level=data["level"],
            )
        except CompressError as exc:
            return error_response(
                str(exc), error_code="VALIDATION_ERROR", status_code=400,
            )

        if job.status == CompressJob.Status.FAILED:
            return error_response(
                "Unable to compress PDF.",
                error_code="PDF_PROCESSING_FAILED",
                status_code=500,
            )

        download_path = reverse("pdf_compress:compress-download", args=[job.id])
        download_url = request.build_absolute_uri(
            f"{download_path}?token={job.owner_token}"
        )

        return success_response(
            "PDF compressed successfully",
            data={
                "file_id": str(job.id),
                "owner_token": job.owner_token,
                "download_url": download_url,
                "filename": "compressed.pdf",
                "page_count": job.page_count,
                "level": job.level,
                "original_size": job.original_size,
                "compressed_size": job.compressed_size,
                "saved_size": job.saved_size,
                "reduction_percent": job.reduction_percent,
            },
            status_code=201,
        )


class CompressJobDownloadView(OwnedJobDownloadView):
    """
    GET /api/v1/pdf/compress/<job_id>/download/?token=<owner_token>

    See apps.common.ownership for the access-control policy this enforces.
    """

    model = CompressJob

    def get_filename(self, job):
        return "compressed.pdf"
