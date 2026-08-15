from django.urls import path

from .views import RotateJobDownloadView, RotatePDFView

app_name = "pdf_rotate"

urlpatterns = [
    path("rotate/", RotatePDFView.as_view(), name="pdf-rotate"),
    path("rotate/<str:job_id>/download/", RotateJobDownloadView.as_view(), name="rotate-download"),
]
