from django.urls import path

from .views import CompressJobDownloadView, CompressPDFView

app_name = "pdf_compress"

urlpatterns = [
    path("compress/", CompressPDFView.as_view(), name="pdf-compress"),
    path("compress/<str:job_id>/download/", CompressJobDownloadView.as_view(), name="compress-download"),
]
