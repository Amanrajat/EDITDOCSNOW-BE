from django.core.exceptions import ValidationError
from django.http import FileResponse, JsonResponse
from rest_framework.views import APIView

from .ownership import is_owner
from .responses import error_response


def _check_database():
    from django.db import connection

    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")


def _check_redis():
    import redis
    from django.conf import settings

    client = redis.from_url(settings.REDIS_URL, socket_connect_timeout=2, socket_timeout=2)
    try:
        client.ping()
    finally:
        client.close()


def health_check(request):
    """
    GET /health/

    Plain liveness check by default: no DB/Redis/storage access, just a
    fast 200. This is what a platform's healthcheck (e.g. Render's
    `healthCheckPath`) polls to decide whether to route traffic to this
    instance at all, so it must stay cheap regardless of the state of
    anything else the app depends on - a slow or down dependency should
    surface as a failure of the specific feature that needs it, not as
    this instance being killed/recycled.

    GET /health/?deep=true

    Opt-in dependency check for manual/diagnostic use (not meant to be
    the platform's automated probe - a broker or DB hiccup here should
    not by itself get a healthy web instance recycled). Actually
    connects to Postgres and Redis and reports per-dependency status.
    Returns 200 if every checked dependency is reachable, 503 otherwise.
    """
    if request.GET.get("deep") != "true":
        return JsonResponse({"status": "ok"})

    checks = {}
    all_ok = True

    for name, check in (("database", _check_database), ("redis", _check_redis)):
        try:
            check()
            checks[name] = "ok"
        except Exception as exc:
            checks[name] = f"error: {exc}"
            all_ok = False

    return JsonResponse(
        {"status": "ok" if all_ok else "degraded", "checks": checks},
        status=200 if all_ok else 503,
    )


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
