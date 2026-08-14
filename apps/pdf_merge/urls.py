from django.urls import path

from .views import MergeJobDownloadView, MergePDFView

app_name = "pdf_merge"

urlpatterns = [
    path("merge/", MergePDFView.as_view(), name="pdf-merge"),
    path("merge/<str:job_id>/download/", MergeJobDownloadView.as_view(), name="merge-download"),
]
