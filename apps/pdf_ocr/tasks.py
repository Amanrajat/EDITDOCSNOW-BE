from celery import shared_task

from .services import process_job


@shared_task(bind=True, max_retries=2, default_retry_delay=5)
def run_ocr_job(self, job_id):
    """Thin wrapper around services.process_job (plain sync code) so the
    actual logic is unit-testable without Celery/a broker at all. Genuine
    OCR failures (encrypted PDF, unsupported language, etc.) are already
    caught inside process_job and recorded as a failed job - retry only
    matters for true infrastructure hiccups that raise before/after that
    inner try/except."""
    try:
        process_job(job_id)
    except Exception as exc:
        raise self.retry(exc=exc)
