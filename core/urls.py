from django.contrib import admin
from django.urls import path, include

from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    path('admin/', admin.site.urls),

    path(
        'docs_editor/',
        include('apps.docs_editor.urls')
    ),
]

# Static files are served by whitenoise regardless of DEBUG. Media (uploaded/
# generated PDFs) has no object storage configured, so Django serves it
# directly in every environment — fine for this app's traffic level.
urlpatterns += static(
    settings.MEDIA_URL,
    document_root=settings.MEDIA_ROOT
)