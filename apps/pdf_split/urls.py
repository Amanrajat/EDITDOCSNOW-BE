from django.urls import path

from .views import SplitJobDownloadView, SplitPDFView

app_name = "pdf_split"

urlpatterns = [
    path("split/", SplitPDFView.as_view(), name="pdf-split"),
    path("split/<str:job_id>/download/", SplitJobDownloadView.as_view(), name="split-download"),
]
