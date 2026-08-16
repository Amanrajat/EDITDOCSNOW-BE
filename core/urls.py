import re

from django.contrib import admin
from django.urls import path, include, re_path

from django.conf import settings
from django.views.static import serve as serve_static

from apps.common.views import health_check

urlpatterns = [
    path('admin/', admin.site.urls),

    path('health/', health_check, name='health-check'),

    path(
        'docs_editor/',
        include('apps.docs_editor.urls')
    ),

    # New PDF-processing features are mounted under a versioned prefix,
    # separate from the pre-existing /docs_editor/ routes above.
    path('api/v1/pdf/', include('apps.pdf_merge.urls')),
    path('api/v1/pdf/', include('apps.pdf_split.urls')),
    path('api/v1/pdf/', include('apps.pdf_organize.urls')),
    path('api/v1/pdf/', include('apps.pdf_remove_pages.urls')),
    path('api/v1/pdf/', include('apps.pdf_rotate.urls')),
    path('api/v1/pdf/', include('apps.pdf_crop.urls')),
    path('api/v1/pdf/', include('apps.pdf_page_numbers.urls')),
    path('api/v1/pdf/', include('apps.pdf_compress.urls')),
    path('api/v1/pdf/', include('apps.pdf_batch.urls')),
    path('api/v1/pdf/', include('apps.pdf_convert.urls')),
    path('api/v1/pdf/', include('apps.pdf_ocr.urls')),
]

# Static files are served by whitenoise regardless of DEBUG. Media (uploaded/
# generated PDFs) has no object storage configured, so Django serves it
# directly in every environment - fine for this app's traffic level.
#
# django.conf.urls.static.static() looks like it does this, but it silently
# returns an empty urlpatterns list whenever DEBUG=False (that's a built-in
# guard in Django itself, not something this file controls), which made every
# /media/... download 404 in production. Registering the underlying `serve`
# view directly bypasses that guard.
urlpatterns += [
    re_path(
        r'^%s(?P<path>.*)$' % re.escape(settings.MEDIA_URL.lstrip('/')),
        serve_static,
        {'document_root': settings.MEDIA_ROOT},
    ),
]