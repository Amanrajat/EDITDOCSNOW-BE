from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.pdf_crop.models import CropJob


class Command(BaseCommand):
    help = (
        "Delete CropJob records (and their output files on disk) older "
        "than --days (default 7)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--days", type=int, default=7)

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(days=options["days"])
        old_jobs = CropJob.objects.filter(created_at__lt=cutoff)
        count = old_jobs.count()

        for job in old_jobs:
            if job.output_file:
                job.output_file.delete(save=False)

        old_jobs.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f"Deleted {count} crop job(s) older than {options['days']} day(s)."
            )
        )
