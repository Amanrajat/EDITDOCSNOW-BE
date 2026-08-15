from django.urls import path

from .views import PageNumberJobDownloadView, PageNumberView

app_name = "pdf_page_numbers"

urlpatterns = [
    path("page-numbers/", PageNumberView.as_view(), name="pdf-page-numbers"),
    path("page-numbers/<str:job_id>/download/", PageNumberJobDownloadView.as_view(), name="page-numbers-download"),
]
