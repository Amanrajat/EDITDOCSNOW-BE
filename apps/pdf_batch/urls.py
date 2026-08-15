from django.urls import path

from .views import BatchCompressView, BatchDownloadView, BatchStatusView

app_name = "pdf_batch"

urlpatterns = [
    path("batch/compress/", BatchCompressView.as_view(), name="batch-compress"),
    path("batch/<str:batch_id>/status/", BatchStatusView.as_view(), name="batch-status"),
    path("batch/<str:batch_id>/download/", BatchDownloadView.as_view(), name="batch-download"),
]
