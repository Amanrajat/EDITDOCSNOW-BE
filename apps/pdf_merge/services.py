import fitz
from django.core.files.base import ContentFile

from apps.common.ownership import generate_owner_token

from .models import MergeJob


class MergePDFService:

    @staticmethod
    def merge(files, order=None):
        """
        Merge `files` (a list of Django UploadedFile objects, already
        validated as real PDFs) into one PDF, in `order` (a permutation of
        0..len(files)-1) if given, else upload order.

        Returns (merged_pdf_bytes, total_page_count).
        """
        sequence = order if order is not None else range(len(files))

        merged = fitz.open()
        try:
            for index in sequence:
                source_file = files[index]
                source_file.seek(0)
                data = source_file.read()

                with fitz.open(stream=data, filetype="pdf") as source:
                    merged.insert_pdf(source)

            total_pages = len(merged)
            output_bytes = merged.tobytes(garbage=4, deflate=True)
            return output_bytes, total_pages
        finally:
            merged.close()

    @classmethod
    def run(cls, user, files, order=None):
        """
        Full orchestration: create a MergeJob row, run the merge, persist
        the result (or the failure reason), and return the job.
        """
        job = MergeJob.objects.create(
            user=user,
            owner_token=generate_owner_token(),
            source_filenames=[f.name for f in files],
            source_count=len(files),
        )

        try:
            output_bytes, total_pages = cls.merge(files, order=order)
        except Exception as exc:
            job.status = MergeJob.Status.FAILED
            job.error_message = str(exc)
            job.save(update_fields=["status", "error_message"])
            return job

        job.output_file.save(
            f"{job.id}.pdf", ContentFile(output_bytes), save=False,
        )
        job.total_pages = total_pages
        job.status = MergeJob.Status.COMPLETED
        job.save(update_fields=["output_file", "total_pages", "status"])

        return job
