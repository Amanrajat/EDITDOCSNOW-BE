from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.pdf_batch.models import BatchJob


class Command(BaseCommand):
    help = (
        "Delete BatchJob records (and their per-file output files plus "
        "the batch ZIP) older than --days (default 7)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options["days"])
        old_batches = BatchJob.objects.filter(created_at__lt=cutoff)
        count = old_batches.count()

        for batch in old_batches:
            for file_job in batch.files.all():
                if file_job.source_file:
                    file_job.source_file.delete(save=False)
                if file_job.output_file:
                    file_job.output_file.delete(save=False)
            if batch.output_zip:
                batch.output_zip.delete(save=False)

        old_batches.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {count} batch job(s) older than {options['days']} day(s)."
            )
        )
