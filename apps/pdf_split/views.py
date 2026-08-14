from django.urls import reverse
from rest_framework.views import APIView

from apps.common.responses import error_response, success_response
from apps.common.views import OwnedJobDownloadView

from .models import SplitJob
from .serializers import SplitPDFRequestSerializer
from .services import SplitError, SplitPDFService


class SplitPDFView(APIView):
    """
    POST /api/v1/pdf/split/

    multipart/form-data:
        file:   a single PDF
        mode:   "all_pages" | "ranges" | "every_n" | "extract"
        ranges: required for mode=ranges, e.g. "1-5,6-10,11"
        n:      required for mode=every_n
        pages:  required for mode=extract, e.g. pages=1&pages=3&pages=5
    """

    def post(self, request):
        serializer = SplitPDFRequestSerializer(data=request.data)

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
            job = SplitPDFService.run(
                user=user,
                uploaded_file=data["file"],
                mode=data["mode"],
                ranges_text=data.get("ranges"),
                n=data.get("n"),
                pages=data.get("pages"),
            )
        except SplitError as exc:
            return error_response(
                str(exc), error_code="VALIDATION_ERROR", status_code=400,
            )

        if job.status == SplitJob.Status.FAILED:
            return error_response(
                "Unable to split PDF.",
                error_code="PDF_PROCESSING_FAILED",
                status_code=500,
            )

        download_path = reverse("pdf_split:split-download", args=[job.id])
        download_url = request.build_absolute_uri(
            f"{download_path}?token={job.owner_token}"
        )

        return success_response(
            "PDF split successfully",
            data={
                "file_id": str(job.id),
                "owner_token": job.owner_token,
                "download_url": download_url,
                "filename": "split_result.zip" if job.is_zip else job.output_filenames[0],
                "is_zip": job.is_zip,
                "output_count": job.output_count,
                "output_filenames": job.output_filenames,
                "source_pages": job.source_pages,
            },
            status_code=201,
        )


class SplitJobDownloadView(OwnedJobDownloadView):
    """
    GET /api/v1/pdf/split/<job_id>/download/?token=<owner_token>

    See apps.common.ownership for the access-control policy this enforces.
    """

    model = SplitJob

    def get_filename(self, job):
        return "split_result.zip" if job.is_zip else job.output_filenames[0]

    def get_content_type(self, job):
        return "application/zip" if job.is_zip else "application/pdf"
