from django.urls import reverse
from rest_framework.views import APIView

from apps.common.responses import error_response, success_response
from apps.common.views import OwnedJobDownloadView

from .models import PageNumberJob
from .serializers import PageNumberRequestSerializer
from .services import PageNumberError, PageNumberService


class PageNumberView(APIView):
    """
    POST /api/v1/pdf/page-numbers/

    multipart/form-data:
        file:         a single PDF
        pages:        optional, 1-based page numbers to stamp, repeated
                      fields (e.g. pages=2&pages=3) - omit for every page
        start_number: optional int, default 1
        position:     optional, one of top-left/top-center/top-right/
                      bottom-left/bottom-center/bottom-right, default
                      bottom-center
        font_size:    optional int (6-72), default 12
        font_color:   optional hex color, default #000000
        margin:       optional float (points), default 28
        prefix, suffix: optional literal text around the number
    """

    def post(self, request):
        serializer = PageNumberRequestSerializer(data=request.data)

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
            job = PageNumberService.run(
                user=user,
                uploaded_file=data["file"],
                pages=data.get("pages"),
                start_number=data["start_number"],
                position=data["position"],
                font_size=data["font_size"],
                font_color=data["font_color"],
                margin=data["margin"],
                prefix=data["prefix"],
                suffix=data["suffix"],
            )
        except PageNumberError as exc:
            return error_response(
                str(exc), error_code="VALIDATION_ERROR", status_code=400,
            )

        if job.status == PageNumberJob.Status.FAILED:
            return error_response(
                "Unable to add page numbers.",
                error_code="PDF_PROCESSING_FAILED",
                status_code=500,
            )

        download_path = reverse("pdf_page_numbers:page-numbers-download", args=[job.id])
        download_url = request.build_absolute_uri(
            f"{download_path}?token={job.owner_token}"
        )

        return success_response(
            "Page numbers added successfully",
            data={
                "file_id": str(job.id),
                "owner_token": job.owner_token,
                "download_url": download_url,
                "filename": "numbered.pdf",
                "page_count": job.page_count,
                "numbered_pages": job.numbered_pages,
                "start_number": job.start_number,
                "position": job.position,
            },
            status_code=201,
        )


class PageNumberJobDownloadView(OwnedJobDownloadView):
    """
    GET /api/v1/pdf/page-numbers/<job_id>/download/?token=<owner_token>

    See apps.common.ownership for the access-control policy this enforces.
    """

    model = PageNumberJob

    def get_filename(self, job):
        return "numbered.pdf"
