from django.urls import path

from .views import RemovePagesJobDownloadView, RemovePagesView

app_name = "pdf_remove_pages"

urlpatterns = [
    path("remove-pages/", RemovePagesView.as_view(), name="pdf-remove-pages"),
    path(
        "remove-pages/<str:job_id>/download/",
        RemovePagesJobDownloadView.as_view(),
        name="remove-pages-download",
    ),
]
