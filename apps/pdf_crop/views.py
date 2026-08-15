from django.urls import reverse
from rest_framework.views import APIView

from apps.common.responses import error_response, success_response
from apps.common.views import OwnedJobDownloadView

from .models import CropJob
from .serializers import CropPDFRequestSerializer
from .services import CropError, CropPDFService


class CropPDFView(APIView):
    """
    POST /api/v1/pdf/crop/

    multipart/form-data:
        file:  a single PDF
        pages: optional, 1-based page numbers to crop, repeated fields
               (e.g. pages=1&pages=3) - omit to crop every page
        x0, y0, x1, y1: required, fractions (0..1) of each target page's
               own width/height, top-left origin, y increasing downward
    """

    def post(self, request):
        serializer = CropPDFRequestSerializer(data=request.data)

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
            job = CropPDFService.run(
                user=user,
                uploaded_file=data["file"],
                pages=data.get("pages"),
                x0=data["x0"],
                y0=data["y0"],
                x1=data["x1"],
                y1=data["y1"],
            )
        except CropError as exc:
            return error_response(
                str(exc), error_code="VALIDATION_ERROR", status_code=400,
            )

        if job.status == CropJob.Status.FAILED:
            return error_response(
                "Unable to crop PDF.",
                error_code="PDF_PROCESSING_FAILED",
                status_code=500,
            )

        download_path = reverse("pdf_crop:crop-download", args=[job.id])
        download_url = request.build_absolute_uri(
            f"{download_path}?token={job.owner_token}"
        )

        return success_response(
            "PDF cropped successfully",
            data={
                "file_id": str(job.id),
                "owner_token": job.owner_token,
                "download_url": download_url,
                "filename": "cropped.pdf",
                "page_count": job.page_count,
                "cropped_pages": job.cropped_pages,
                "crop_rect": job.crop_rect,
            },
            status_code=201,
        )


class CropJobDownloadView(OwnedJobDownloadView):
    """
    GET /api/v1/pdf/crop/<job_id>/download/?token=<owner_token>

    See apps.common.ownership for the access-control policy this enforces.
    """

    model = CropJob

    def get_filename(self, job):
        return "cropped.pdf"
