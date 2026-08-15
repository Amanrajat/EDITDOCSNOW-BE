from django.conf import settings
from django.core.files.storage import FileSystemStorage


class PrivateJobStorage(FileSystemStorage):
    """
    FileSystemStorage rooted at BASE_DIR/private_media, but deconstructed
    for migrations WITHOUT that absolute path baked in.

    Plain `FileSystemStorage(location=str(settings.BASE_DIR / "..."))`
    deconstructs into a migration literal like
    `FileSystemStorage(location='/home/alice/project/private_media')` -
    an absolute path tied to whichever checkout first ran makemigrations.
    Every other environment (a teammate's machine, CI, the actual
    production container - whose BASE_DIR is legitimately different) would
    then see a permanently "pending" migration for every job model's
    output_file field, forever, since the frozen path never matches
    settings.BASE_DIR at runtime again. Overriding deconstruct() to emit
    zero args/kwargs keeps `makemigrations --check` clean everywhere,
    since `location` is always recomputed from BASE_DIR at import time
    instead of frozen into migration history.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("location", str(settings.BASE_DIR / "private_media"))
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        # Path to the CLASS (not the `private_job_storage` singleton
        # instance below) with no args/kwargs - migration code becomes
        # `PrivateJobStorage()`, which re-resolves `location` from
        # BASE_DIR at whatever time/environment it's actually run in,
        # rather than freezing today's absolute path into the migration.
        return ("apps.common.storage.PrivateJobStorage", [], {})


# Output files for job-based features (merge, split, ...) are deliberately
# NOT servable via any public URL - core/urls.py's /media/<path> pattern
# only covers MEDIA_ROOT, and this storage lives outside it. The only way
# to retrieve a file stored here is through an ownership-checked download
# view (apps.common.views.OwnedJobDownloadView), which reads it directly
# via the storage API rather than any HTTP path lookup. This is what makes
# the per-job bearer token (apps.common.ownership) meaningful - without
# this, the token could be bypassed entirely by guessing the job's UUID and
# requesting its file straight from /media/.
private_job_storage = PrivateJobStorage()
