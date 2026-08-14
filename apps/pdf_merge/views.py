from django.urls import reverse
from rest_framework.views import APIView

from apps.common.responses import error_response, success_response
from apps.common.views import OwnedJobDownloadView

from .models import MergeJob
from .serializers import MergePDFRequestSerializer
from .services import MergePDFService


class MergePDFView(APIView):
    """
    POST /api/v1/pdf/merge/

    multipart/form-data:
        files: 2+ PDF files (repeated "files" fields)
        order: optional JSON/repeated-field list of 0-based indices into
               `files`, to merge in a different order than uploaded.
    """

    def post(self, request):
        serializer = MergePDFRequestSerializer(data=request.data)

        if not serializer.is_valid():
            return error_response(
                "Invalid request.",
                error_code="VALIDATION_ERROR",
                status_code=400,
                errors=serializer.errors,
            )

        files = serializer.validated_data["files"]
        order = serializer.validated_data.get("order")

        user = request.user if request.user.is_authenticated else None
        job = MergePDFService.run(user=user, files=files, order=order)

        if job.status == MergeJob.Status.FAILED:
            return error_response(
                "Unable to merge PDFs.",
                error_code="PDF_PROCESSING_FAILED",
                status_code=500,
            )

        download_path = reverse("pdf_merge:merge-download", args=[job.id])
        download_url = request.build_absolute_uri(
            f"{download_path}?token={job.owner_token}"
        )

        return success_response(
            "PDFs merged successfully",
            data={
                "file_id": str(job.id),
                "owner_token": job.owner_token,
                "download_url": download_url,
                "filename": "merged.pdf",
                "source_count": job.source_count,
                "total_pages": job.total_pages,
            },
            status_code=201,
        )


class MergeJobDownloadView(OwnedJobDownloadView):
    """
    GET /api/v1/pdf/merge/<job_id>/download/?token=<owner_token>

    See apps.common.ownership for the access-control policy this enforces.
    """

    model = MergeJob

    def get_filename(self, job):
        return "merged.pdf"
