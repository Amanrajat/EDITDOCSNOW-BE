#!/bin/sh
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput

# Start one Celery worker process to keep memory usage low
celery -A core worker --loglevel=info --concurrency=1 --max-tasks-per-child=1 &

# Start Django web server
exec gunicorn core.wsgi:application \
    --bind 0.0.0.0:${PORT:-8000} \
    --workers ${WEB_CONCURRENCY:-1} \
    --timeout 120