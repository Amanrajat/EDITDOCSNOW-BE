import os
import tempfile
import uuid as uuid_module

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.http import FileResponse
from django.urls import reverse

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response

from apps.common.ownership import generate_owner_token, is_owner
from apps.common.responses import error_response

from .models import Document, DocumentObject
from .serializers import (
    DocumentSerializer,
    DocumentUploadSerializer,
    DocumentBlockSerializer,
    DocumentObjectSerializer,
    DocumentObjectWriteSerializer,
    SaveEditedBlocksSerializer,
)

from .services import (
    DocumentService,
    BlockExtractionService,
    BlockUpdateService,
)

from .pdf_regenerator import regenerate_pdf_with_objects


def _get_owned_document(request, document_id):
    """
    Same access-control shape as every other feature's ownership check
    (apps.common.ownership) - a non-existent document and a wrong/missing
    token both return None here, so the view returns an identical 404 for
    both (prevents document-ID enumeration). Malformed UUIDs are caught,
    never a 500.
    """
    try:
        document = Document.objects.get(id=document_id)
    except (Document.DoesNotExist, ValueError, ValidationError):
        return None

    if not is_owner(request, document):
        return None

    return document


def _not_found_response():
    return error_response(
        "No such document, or you don't have access to it.",
        error_code="NOT_FOUND", status_code=404,
    )


class DocumentUploadView(APIView):

    def post(self, request):

        serializer = DocumentUploadSerializer(
            data=request.data
        )

        serializer.is_valid(
            raise_exception=True
        )

        uploaded_file = serializer.validated_data[
            "original_file"
        ]

        document = Document.objects.create(
            user=request.user
            if request.user.is_authenticated
            else None,
            owner_token=generate_owner_token(),
            original_file=uploaded_file,
            original_name=uploaded_file.name,
            file_size=uploaded_file.size,
            file_type=Document.FileType.PDF,
            status=Document.Status.UPLOADED,
        )

        try:
            DocumentService.update_document_metadata(
                document
            )
        except Exception as exc:
            document.status = Document.Status.FAILED
            document.error_message = str(exc)

            document.save(
                update_fields=[
                    "status",
                    "error_message",
                ]
            )

            return Response(
                {**DocumentSerializer(document).data, "owner_token": document.owner_token},
                status=status.HTTP_201_CREATED,
            )

        return Response(
            {**DocumentSerializer(document).data, "owner_token": document.owner_token},
            status=status.HTTP_201_CREATED,
        )


class DocumentDetailView(APIView):

    def get(self, request, document_id):

        document = _get_owned_document(request, document_id)
        if document is None:
            return _not_found_response()

        serializer = DocumentSerializer(
            document
        )

        return Response(serializer.data)


class ExtractBlocksView(APIView):

    def post(self, request, document_id):

        document = _get_owned_document(request, document_id)
        if document is None:
            return _not_found_response()

        blocks = (
            BlockExtractionService
            .extract_and_store(document)
        )

        serializer = DocumentBlockSerializer(
            blocks,
            many=True
        )

        return Response(
            {
                "document_id": str(document.id),
                "total_blocks": len(serializer.data),
                "blocks": serializer.data,
            },
            status=status.HTTP_200_OK,
        )


class SaveEditedBlocksView(APIView):

    def post(self, request, document_id):

        document = _get_owned_document(request, document_id)
        if document is None:
            return _not_found_response()

        serializer = SaveEditedBlocksSerializer(
            data=request.data
        )

        serializer.is_valid(
            raise_exception=True
        )

        BlockUpdateService.update_blocks(
            document=document,
            blocks_data=serializer.validated_data[
                "blocks"
            ],
        )

        # Only regenerate blocks the user actually edited - every
        # other block is left byte-for-byte untouched in the output
        # PDF so the rest of the document keeps its original fonts,
        # spacing, and layout.
        pdf_blocks = [
            {
                "page": block.page_number,
                "text": block.text,
                "bbox": block.bbox,
                "font_name": block.font_name,
                "size": block.font_size,
                "color": block.color,
                "is_bold": block.is_bold,
                "is_italic": block.is_italic,
            }
            for block in document.blocks.all()
            if block.text != block.original_text
        ]

        # Every current object (text/image/shape/freehand stroke) is
        # rendered every save - unlike blocks, objects have no "original"
        # state to diff against, they're purely additive. Each dict
        # carries its own originating object's id (`_id`) so
        # get_image_bytes can resolve image bytes without threading model
        # instances into object_renderer.py, which only knows plain dicts.
        editor_objects = list(document.editor_objects.all())
        pdf_objects = [
            {
                "_id": str(obj.id),
                "page_number": obj.page_number,
                "object_type": obj.object_type,
                "bbox": obj.bbox,
                "points": obj.points,
                "rotation": obj.rotation,
                "opacity": obj.opacity,
                "fill_color": obj.fill_color,
                "stroke_color": obj.stroke_color,
                "stroke_width": obj.stroke_width,
                "text_content": obj.text_content,
                "font_family": obj.font_family,
                "font_size": obj.font_size,
                "is_bold": obj.is_bold,
                "is_italic": obj.is_italic,
                "text_align": obj.text_align,
            }
            for obj in editor_objects
        ]
        images_by_object_id = {
            str(obj.id): obj.image_file.read()
            for obj in editor_objects
            if obj.object_type == DocumentObject.ObjectType.IMAGE and obj.image_file
        }

        def get_image_bytes(pdf_object):
            return images_by_object_id.get(pdf_object.get("_id"))

        output_bytes_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp_output:
                output_bytes_path = tmp_output.name

            regenerate_pdf_with_objects(
                input_path=document.original_file.path,
                output_path=output_bytes_path,
                blocks=pdf_blocks,
                objects=pdf_objects,
                get_image_bytes=get_image_bytes,
            )

            with open(output_bytes_path, "rb") as f:
                output_bytes = f.read()
        except Exception as exc:
            document.status = Document.Status.FAILED
            document.error_message = str(exc)

            document.save(
                update_fields=[
                    "status",
                    "error_message",
                ]
            )

            return Response(
                {
                    "document_id": str(document.id),
                    "error": "Failed to regenerate PDF.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        finally:
            if output_bytes_path and os.path.exists(output_bytes_path):
                os.remove(output_bytes_path)

        document.edited_file.save(
            f"edited_{uuid_module.uuid4().hex}.pdf", ContentFile(output_bytes), save=False,
        )
        document.status = (
            Document.Status.COMPLETED
        )
        document.error_message = ""

        document.save(
            update_fields=[
                "edited_file",
                "status",
                "error_message",
            ]
        )

        download_path = reverse("docs_editor:document-download", args=[document.id])
        download_url = request.build_absolute_uri(
            f"{download_path}?token={document.owner_token}"
        )

        return Response(
            {
                "document_id": str(document.id),
                "download_url": download_url,
            },
            status=status.HTTP_200_OK,
        )


class DocumentDownloadView(APIView):
    """
    GET /docs_editor/<document_id>/download/?token=<owner_token>

    Serves the regenerated (edited) PDF - private storage, token-gated,
    same security shape as every other feature's download endpoint. Not a
    subclass of apps.common.views.OwnedJobDownloadView since that assumes
    a field named `output_file`; this model's equivalent is `edited_file`
    (pre-existing name, kept as-is rather than renamed).
    """

    def get(self, request, document_id):
        document = _get_owned_document(request, document_id)
        if document is None:
            return _not_found_response()

        if document.status != Document.Status.COMPLETED or not document.edited_file:
            return error_response(
                "This file is not ready for download.",
                error_code="NOT_READY", status_code=404,
            )

        as_attachment = request.query_params.get("disposition") != "inline"
        return FileResponse(
            document.edited_file.open("rb"),
            as_attachment=as_attachment,
            filename=f"edited_{document.original_name}",
            content_type="application/pdf",
        )


class DocumentObjectListCreateView(APIView):
    """
    GET  /docs_editor/<document_id>/objects/?token=...   - list every object
    POST /docs_editor/<document_id>/objects/?token=...   - create one

    multipart/form-data (POST):
        page_number, object_type, z_index, bbox (JSON array as a string
        or repeated fields - see serializer), points, rotation, opacity,
        fill_color, stroke_color, stroke_width, text_content, font_family,
        font_size, is_bold, is_italic, text_align, image (file, required
        for object_type="image")
    """

    def get(self, request, document_id):
        document = _get_owned_document(request, document_id)
        if document is None:
            return _not_found_response()

        objects = document.editor_objects.all()
        return Response(DocumentObjectSerializer(objects, many=True, context={"request": request}).data)

    def post(self, request, document_id):
        document = _get_owned_document(request, document_id)
        if document is None:
            return _not_found_response()

        serializer = DocumentObjectWriteSerializer(data=request.data, context={"document": document})
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()

        return Response(
            DocumentObjectSerializer(instance, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class DocumentObjectDetailView(APIView):
    """
    PATCH  /docs_editor/<document_id>/objects/<object_id>/?token=...
    DELETE /docs_editor/<document_id>/objects/<object_id>/?token=...
    """

    def _get_owned_object(self, request, document_id, object_id):
        document = _get_owned_document(request, document_id)
        if document is None:
            return None, None
        try:
            instance = document.editor_objects.get(id=object_id)
        except (DocumentObject.DoesNotExist, ValueError, ValidationError):
            return document, None
        return document, instance

    def patch(self, request, document_id, object_id):
        document, instance = self._get_owned_object(request, document_id, object_id)
        if document is None:
            return _not_found_response()
        if instance is None:
            return error_response("No such object.", error_code="NOT_FOUND", status_code=404)

        serializer = DocumentObjectWriteSerializer(
            instance, data=request.data, partial=True, context={"document": document},
        )
        serializer.is_valid(raise_exception=True)
        instance = serializer.save()

        return Response(DocumentObjectSerializer(instance, context={"request": request}).data)

    def delete(self, request, document_id, object_id):
        document, instance = self._get_owned_object(request, document_id, object_id)
        if document is None:
            return _not_found_response()
        if instance is None:
            return error_response("No such object.", error_code="NOT_FOUND", status_code=404)

        if instance.image_file:
            instance.image_file.delete(save=False)
        instance.delete()

        return Response(status=status.HTTP_204_NO_CONTENT)


class DocumentObjectImageView(APIView):
    """
    GET /docs_editor/<document_id>/objects/<object_id>/image/?token=...

    Serves a single object's uploaded image - private storage, token-gated,
    same shape as DocumentDownloadView. image_file.url is not servable
    directly (private_job_storage lives outside MEDIA_ROOT), so
    DocumentObjectSerializer points every image object at this endpoint
    instead of the raw FileField URL.
    """

    def get(self, request, document_id, object_id):
        document = _get_owned_document(request, document_id)
        if document is None:
            return _not_found_response()

        try:
            instance = document.editor_objects.get(id=object_id)
        except (DocumentObject.DoesNotExist, ValueError, ValidationError):
            return error_response("No such object.", error_code="NOT_FOUND", status_code=404)

        if not instance.image_file:
            return error_response("This object has no image.", error_code="NOT_FOUND", status_code=404)

        return FileResponse(
            instance.image_file.open("rb"),
            as_attachment=False,
            filename=os.path.basename(instance.image_file.name),
        )
