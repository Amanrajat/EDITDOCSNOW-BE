from django.core.exceptions import ValidationError
from django.http import FileResponse, JsonResponse
from rest_framework.views import APIView

from .ownership import is_owner
from .responses import error_response


def health_check(request):
    """
    GET /health/

    Deliberately dependency-free (no DB/Redis/storage checks) - this is
    what a platform's healthcheck (e.g. Render's `healthCheckPath`) polls
    to decide whether to route traffic to this instance at all, so it
    must stay fast and cheap regardless of the state of anything else the
    app depends on. A slow or down dependency should surface as a failure
    of the specific feature that needs it, not as this instance being
    killed/recycled.
    """
    return JsonResponse({"status": "ok"})


class OwnedJobDownloadView(APIView):
    """
    Shared "download this job's result, if you own it" view, reused by
    every job-based feature (Merge, Split, ...).

    Subclasses set:
        model             - the Job model (needs id/status/output_file/
                             user/owner_token fields)
        completed_status  - the Status value meaning "ready" (default
                             "completed", matches every job model so far)

    and implement:
        get_filename(job)      -> str
        get_content_type(job)  -> str (default: "application/pdf")
    """

    model = None
    completed_status = "completed"

    def get(self, request, job_id):
        job = self._get_owned_job(request, job_id)

        if job is None:
            # Deliberately identical to the "not ready" response below in
            # shape (404, same error_code) - see apps.common.ownership's
            # module docstring for why non-existence and wrong-owner must
            # not be distinguishable.
            return error_response(
                "No such file, or you don't have access to it.",
                error_code="NOT_FOUND",
                status_code=404,
            )

        if job.status != self.completed_status or not job.output_file:
            return error_response(
                "This file is not ready for download.",
                error_code="NOT_READY",
                status_code=404,
            )

        # Cross-origin <a download> is ignored by browsers, so whether
        # "Download" vs. "Open" behaves as a save-to-disk vs. an inline
        # view is actually decided here, not by the frontend.
        as_attachment = request.query_params.get("disposition") != "inline"

        return FileResponse(
            job.output_file.open("rb"),
            as_attachment=as_attachment,
            filename=self.get_filename(job),
            content_type=self.get_content_type(job),
        )

    def _get_owned_job(self, request, job_id):
        try:
            job = self.model.objects.get(id=job_id)
        except (self.model.DoesNotExist, ValueError, ValidationError):
            return None

        if not is_owner(request, job):
            return None

        return job

    def get_filename(self, job):
        raise NotImplementedError

    def get_content_type(self, job):
        return "application/pdf"
