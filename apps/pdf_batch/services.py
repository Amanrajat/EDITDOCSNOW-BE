import zipfile
from io import BytesIO

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.common.ownership import generate_owner_token
from apps.common.validation import validate_pdf_file
from apps.pdf_compress.services import CompressPDFService

from .models import BatchFileJob, BatchJob

MAX_BATCH_FILES = 30


class BatchError(Exception):
    """A user-facing, 400-worthy error at the whole-batch level (e.g. too
    many files) - per-file problems are isolated onto that file's own
    BatchFileJob instead of rejecting the batch."""


class BatchService:
    """
    Orchestrates a batch of same-operation jobs. Each file is validated
    individually at submission time - a bad file is marked failed on its
    own BatchFileJob rather than rejecting every other file in the batch
    ("isolate failures", per the batch processing spec). Valid files are
    queued as Celery tasks (apps.pdf_batch.tasks.process_batch_file);
    actual processing reuses the exact same service classes as the
    synchronous single-file tools (e.g. CompressPDFService) rather than
    duplicating that logic.
    """

    @staticmethod
    def create_batch(user, uploaded_files, operation, options):
        if not uploaded_files:
            raise BatchError("At least one file is required.")
        if len(uploaded_files) > MAX_BATCH_FILES:
            raise BatchError(f"At most {MAX_BATCH_FILES} files can be processed in one batch.")

        batch = BatchJob.objects.create(
            user=user,
            owner_token=generate_owner_token(),
            operation=operation,
            options=options,
            total_files=len(uploaded_files),
            status=BatchJob.Status.QUEUED,
        )

        file_jobs = []
        for index, uploaded_file in enumerate(uploaded_files):
            file_job = BatchFileJob.objects.create(
                batch=batch,
                order=index,
                original_filename=uploaded_file.name,
            )
            try:
                validate_pdf_file(uploaded_file)
            except ValidationError as exc:
                file_job.status = BatchFileJob.Status.FAILED
                file_job.error_message = "; ".join(str(m) for m in exc.detail) if hasattr(exc, "detail") else str(exc)
                file_job.save(update_fields=["status", "error_message"])
                file_jobs.append(file_job)
                continue

            uploaded_file.seek(0)
            file_job.source_file.save(
                f"{file_job.id}.pdf", ContentFile(uploaded_file.read()), save=False,
            )
            file_job.status = BatchFileJob.Status.QUEUED
            file_job.save(update_fields=["source_file", "status"])
            file_jobs.append(file_job)

        batch.status = BatchJob.Status.PROCESSING
        batch.save(update_fields=["status"])

        return batch, file_jobs

    @staticmethod
    def process_file(file_job_id):
        """Runs the actual per-file operation - called from the Celery
        task, but is plain sync code so it's trivially unit-testable and
        also runnable with CELERY_TASK_ALWAYS_EAGER in tests."""
        try:
            file_job = BatchFileJob.objects.select_related("batch").get(id=file_job_id)
        except BatchFileJob.DoesNotExist:
            return

        if file_job.status != BatchFileJob.Status.QUEUED:
            return  # already processed (e.g. duplicate task delivery)

        file_job.status = BatchFileJob.Status.PROCESSING
        file_job.save(update_fields=["status"])

        try:
            source_bytes = file_job.source_file.read()
            options = file_job.batch.options or {}

            if file_job.batch.operation == BatchJob.Operation.COMPRESS:
                level = options.get("level", "recommended")
                output_bytes, page_count, original_size, compressed_size = CompressPDFService.compress(
                    source_bytes, level,
                )
            else:
                raise ValueError(f"Unsupported batch operation: {file_job.batch.operation!r}")

            file_job.page_count = page_count
            file_job.original_size = original_size
            file_job.compressed_size = compressed_size
            file_job.output_file.save(
                f"{file_job.id}.pdf", ContentFile(output_bytes), save=False,
            )
            file_job.status = BatchFileJob.Status.COMPLETED
            file_job.save(update_fields=[
                "page_count", "original_size", "compressed_size", "output_file", "status",
            ])
        except Exception as exc:
            file_job.status = BatchFileJob.Status.FAILED
            file_job.error_message = str(exc)
            file_job.save(update_fields=["status", "error_message"])
        finally:
            # Source file is temporary - clean it up regardless of outcome.
            # save=True: the field must actually persist as cleared, not
            # just deleted from storage while the DB row still points at it.
            if file_job.source_file:
                file_job.source_file.delete(save=True)

        BatchService.finalize_if_done(file_job.batch_id)

    @staticmethod
    def finalize_if_done(batch_id):
        """Recomputes the parent BatchJob's overall status once every
        child file has reached a terminal state, and builds the combined
        ZIP of whatever succeeded. select_for_update serializes concurrent
        workers finishing sibling files at nearly the same time so only
        one of them actually does the finalize work."""
        with transaction.atomic():
            batch = BatchJob.objects.select_for_update().get(id=batch_id)

            if batch.status in (BatchJob.Status.COMPLETED, BatchJob.Status.PARTIAL, BatchJob.Status.FAILED):
                return  # already finalized

            files = list(batch.files.all())
            if any(f.status in (BatchFileJob.Status.QUEUED, BatchFileJob.Status.PROCESSING) for f in files):
                return  # still work in progress

            completed = [f for f in files if f.status == BatchFileJob.Status.COMPLETED]
            failed = [f for f in files if f.status == BatchFileJob.Status.FAILED]

            if completed:
                buffer = BytesIO()
                with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                    used_names = set()
                    for file_job in completed:
                        base_name = file_job.original_filename or f"{file_job.id}.pdf"
                        name = base_name
                        suffix = 1
                        while name in used_names:
                            name = f"{base_name}_{suffix}"
                            suffix += 1
                        used_names.add(name)
                        file_job.output_file.open("rb")
                        try:
                            zf.writestr(name, file_job.output_file.read())
                        finally:
                            file_job.output_file.close()
                batch.output_zip.save(f"{batch.id}.zip", ContentFile(buffer.getvalue()), save=False)

            if failed and not completed:
                batch.status = BatchJob.Status.FAILED
            elif failed:
                batch.status = BatchJob.Status.PARTIAL
            else:
                batch.status = BatchJob.Status.COMPLETED

            batch.completed_at = timezone.now()
            batch.save(update_fields=["status", "output_zip", "completed_at"])
