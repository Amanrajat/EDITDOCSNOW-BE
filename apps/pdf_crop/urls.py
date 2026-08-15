from django.urls import path

from .views import CropJobDownloadView, CropPDFView

app_name = "pdf_crop"

urlpatterns = [
    path("crop/", CropPDFView.as_view(), name="pdf-crop"),
    path("crop/<str:job_id>/download/", CropJobDownloadView.as_view(), name="crop-download"),
]
