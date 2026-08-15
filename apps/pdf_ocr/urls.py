from django.urls import path

from .views import OcrDownloadView, OcrStatusView, OcrSubmitView

app_name = "pdf_ocr"

urlpatterns = [
    path("ocr/", OcrSubmitView.as_view(), name="ocr-submit"),
    path("ocr/<str:job_id>/status/", OcrStatusView.as_view(), name="ocr-status"),
    path("ocr/<str:job_id>/download/", OcrDownloadView.as_view(), name="ocr-download"),
]
