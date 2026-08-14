from django.urls import path

from .views import OrganizeJobDownloadView, OrganizePDFView

app_name = "pdf_organize"

urlpatterns = [
    path("organize/", OrganizePDFView.as_view(), name="pdf-organize"),
    path("organize/<str:job_id>/download/", OrganizeJobDownloadView.as_view(), name="organize-download"),
]
