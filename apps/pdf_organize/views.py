from django.urls import reverse
from rest_framework.views import APIView

from apps.common.responses import error_response, success_response
from apps.common.views import OwnedJobDownloadView

from .models import OrganizeJob
from .serializers import OrganizePDFRequestSerializer
from .services import OrganizeError, OrganizePDFService


class OrganizePDFView(APIView):
    """
    POST /api/v1/pdf/organize/

    multipart/form-data:
        file:  a single PDF
        order: the full new page order, 1-based, repeated "order" fields
               (e.g. order=3&order=1&order=5&order=2&order=4)
    """

    def post(self, request):
        serializer = OrganizePDFRequestSerializer(data=request.data)

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
            job = OrganizePDFService.run(
                user=user, uploaded_file=data["file"], order=data["order"],
            )
        except OrganizeError as exc:
            return error_response(
                str(exc), error_code="VALIDATION_ERROR", status_code=400,
            )

        if job.status == OrganizeJob.Status.FAILED:
            return error_response(
                "Unable to organize PDF.",
                error_code="PDF_PROCESSING_FAILED",
                status_code=500,
            )

        download_path = reverse("pdf_organize:organize-download", args=[job.id])
        download_url = request.build_absolute_uri(
            f"{download_path}?token={job.owner_token}"
        )

        return success_response(
            "PDF organized successfully",
            data={
                "file_id": str(job.id),
                "owner_token": job.owner_token,
                "download_url": download_url,
                "filename": "organized.pdf",
                "page_count": job.page_count,
            },
            status_code=201,
        )


class OrganizeJobDownloadView(OwnedJobDownloadView):
    """
    GET /api/v1/pdf/organize/<job_id>/download/?token=<owner_token>

    See apps.common.ownership for the access-control policy this enforces.
    """

    model = OrganizeJob

    def get_filename(self, job):
        return "organized.pdf"
