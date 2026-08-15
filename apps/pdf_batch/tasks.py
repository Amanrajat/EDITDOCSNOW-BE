from celery import shared_task

from .services import BatchService


@shared_task(bind=True, max_retries=2, default_retry_delay=5)
def process_batch_file(self, file_job_id):
    """Processes exactly one file within a batch. Kept as a thin wrapper
    around BatchService.process_file (plain sync code) so the actual
    logic is unit-testable without Celery/a broker at all."""
    try:
        BatchService.process_file(file_job_id)
    except Exception as exc:
        raise self.retry(exc=exc)
