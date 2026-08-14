from django.conf import settings
from django.core.files.storage import FileSystemStorage

# Output files for job-based features (merge, split, ...) are deliberately
# NOT servable via any public URL - core/urls.py's /media/<path> pattern
# only covers MEDIA_ROOT, and this storage lives outside it. The only way
# to retrieve a file stored here is through an ownership-checked download
# view (apps.common.views.OwnedJobDownloadView), which reads it directly
# via the storage API rather than any HTTP path lookup. This is what makes
# the per-job bearer token (apps.common.ownership) meaningful - without
# this, the token could be bypassed entirely by guessing the job's UUID and
# requesting its file straight from /media/.
private_job_storage = FileSystemStorage(
    location=str(settings.BASE_DIR / "private_media"),
)
