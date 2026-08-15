"""
Celery application for EditDocsNow.

Reserved for genuinely long-running or high-volume work (OCR, large
conversions, batch processing) - everything else in this codebase runs
synchronously in the request/response cycle on purpose (see each PDF app's
own docstrings). Broker/result backend is Redis, configured via
CELERY_BROKER_URL / CELERY_RESULT_BACKEND in settings (see core/settings.py).
"""

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "core.settings")

app = Celery("editdocsnow")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
