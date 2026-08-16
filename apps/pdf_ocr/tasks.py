from celery import shared_task

from .services import process_job


@shared_task(bind=True, max_retries=2, default_retry_delay=5)
def run_ocr_job(self, job_id, source_bytes_b64=None):
    """Thin wrapper around services.process_job (plain sync code) so the
    actual logic is unit-testable without Celery/a broker at all. Genuine
    OCR failures (encrypted PDF, unsupported language, etc.) are already
    caught inside process_job and recorded as a failed job - retry only
    matters for true infrastructure hiccups that raise before/after that
    inner try/except.

    `source_bytes_b64` (base64-encoded PDF bytes) is passed through the
    Celery message itself rather than relying on process_job to read
    job.source_file from disk - required whenever the worker doesn't share
    a filesystem with whichever process handled the upload (e.g. web and
    worker as separate Render services with no shared/persistent disk),
    since in that case the file the web dyno wrote is simply not there for
    the worker to read. See services.process_job for the fallback path
    used when this isn't provided (tests, single-process local dev)."""
    try:
        process_job(job_id, source_bytes_b64=source_bytes_b64)
    except Exception as exc:
        raise self.retry(exc=exc)
