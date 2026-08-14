from django.urls import reverse
from rest_framework.views import APIView

from apps.common.responses import error_response, success_response
from apps.common.views import OwnedJobDownloadView

from .models import RemovePagesJob
from .serializers import RemovePagesRequestSerializer
from .services import RemovePagesError, RemovePagesService


class RemovePagesView(APIView):
    """
    POST /api/v1/pdf/remove-pages/

    multipart/form-data:
        file:  a single PDF
        pages: 1-based page numbers to delete, repeated "pages" fields
               (e.g. pages=2&pages=4)
    """

    def post(self, request):
        serializer = RemovePagesRequestSerializer(data=request.data)

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
            job = RemovePagesService.run(
                user=user, uploaded_file=data["file"], pages_to_remove=data["pages"],
            )
        except RemovePagesError as exc:
            return error_response(
                str(exc), error_code="VALIDATION_ERROR", status_code=400,
            )

        if job.status == RemovePagesJob.Status.FAILED:
            return error_response(
                "Unable to remove pages.",
                error_code="PDF_PROCESSING_FAILED",
                status_code=500,
            )

        download_path = reverse("pdf_remove_pages:remove-pages-download", args=[job.id])
        download_url = request.build_absolute_uri(
            f"{download_path}?token={job.owner_token}"
        )

        return success_response(
            "Pages removed successfully",
            data={
                "file_id": str(job.id),
                "owner_token": job.owner_token,
                "download_url": download_url,
                "filename": "pages_removed.pdf",
                "source_page_count": job.source_page_count,
                "removed_pages": job.removed_pages,
                "output_page_count": job.output_page_count,
            },
            status_code=201,
        )


class RemovePagesJobDownloadView(OwnedJobDownloadView):
    """
    GET /api/v1/pdf/remove-pages/<job_id>/download/?token=<owner_token>

    See apps.common.ownership for the access-control policy this enforces.
    """

    model = RemovePagesJob

    def get_filename(self, job):
        return "pages_removed.pdf"
