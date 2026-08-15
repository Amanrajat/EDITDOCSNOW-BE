from django.urls import path

from .views import (
    DocumentUploadView,
    DocumentDetailView,
    DocumentDownloadView,
    DocumentObjectDetailView,
    DocumentObjectImageView,
    DocumentObjectListCreateView,
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

    path(
        "<uuid:document_id>/download/",
        DocumentDownloadView.as_view(),
        name="document-download",
    ),

    path(
        "<uuid:document_id>/objects/",
        DocumentObjectListCreateView.as_view(),
        name="document-objects",
    ),

    path(
        "<uuid:document_id>/objects/<uuid:object_id>/",
        DocumentObjectDetailView.as_view(),
        name="document-object-detail",
    ),

    path(
        "<uuid:document_id>/objects/<uuid:object_id>/image/",
        DocumentObjectImageView.as_view(),
        name="document-object-image",
    ),
]
