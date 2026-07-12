from django.urls import path

from .views import (
    DocumentUploadView,
    DocumentDetailView,
    ExtractBlocksView,
    SaveEditedBlocksView,
)

app_name = "docs_editor"

urlpatterns = [
    path(
        "upload/",
        DocumentUploadView.as_view(),
        name="document-upload",
    ),

    path(
        "<uuid:document_id>/",
        DocumentDetailView.as_view(),
        name="document-detail",
    ),

    path(
        "<uuid:document_id>/extract/",
        ExtractBlocksView.as_view(),
        name="extract-blocks",
    ),

    path(
        "<uuid:document_id>/save/",
        SaveEditedBlocksView.as_view(),
        name="save-edited-blocks",
    ),
]