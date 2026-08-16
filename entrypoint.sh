# #!/bin/sh
# set -e

# python manage.py migrate --noinput
# python manage.py collectstatic --noinput

# exec gunicorn core.wsgi:application \
#     --bind 0.0.0.0:${PORT:-8000} \
#     --workers ${WEB_CONCURRENCY:-3} \
#     --timeout 120


!/bin/sh
set -e

python manage.py migrate --noinput
python manage.py collectstatic --noinput

# Start Celery worker in background
celery -A core worker --loglevel=info &

# Start Django web server
exec gunicorn core.wsgi:application \
    --bind 0.0.0.0:${PORT:-8000} \
    --workers ${WEB_CONCURRENCY:-3} \
    --timeout 120