import os
import uuid

from django.conf import settings
from django.shortcuts import get_object_or_404

from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response

from .models import Document
from .serializers import (
    DocumentSerializer,
    DocumentUploadSerializer,
    DocumentBlockSerializer,
    SaveEditedBlocksSerializer,
)

from .services import (
    DocumentService,
    BlockExtractionService,
    BlockUpdateService,
)

from .pdf_regenerator import regenerate_pdf


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
                DocumentSerializer(document).data,
                status=status.HTTP_201_CREATED,
            )

        return Response(
            DocumentSerializer(document).data,
            status=status.HTTP_201_CREATED,
        )


class DocumentDetailView(APIView):

    def get(self, request, document_id):

        document = get_object_or_404(
            Document,
            id=document_id,
        )

        serializer = DocumentSerializer(
            document
        )

        return Response(serializer.data)


class ExtractBlocksView(APIView):

    def post(self, request, document_id):

        document = get_object_or_404(
            Document,
            id=document_id,
        )

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

        serializer = SaveEditedBlocksSerializer(
            data=request.data
        )

        serializer.is_valid(
            raise_exception=True
        )

        document = get_object_or_404(
            Document,
            id=document_id,
        )

        BlockUpdateService.update_blocks(
            document=document,
            blocks_data=serializer.validated_data[
                "blocks"
            ],
        )

        filename = (
            f"edited_{uuid.uuid4().hex}.pdf"
        )

        relative_path = (
            f"documents/edited/{filename}"
        )

        absolute_path = os.path.join(
            settings.MEDIA_ROOT,
            relative_path,
        )

        os.makedirs(
            os.path.dirname(absolute_path),
            exist_ok=True
        )

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
        ]

        try:
            regenerate_pdf(
                input_path=document.original_file.path,
                output_path=absolute_path,
                blocks=pdf_blocks,
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
                {
                    "document_id": str(document.id),
                    "error": "Failed to regenerate PDF.",
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        document.edited_file = relative_path
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

        return Response(
            {
                "document_id": str(document.id),
                "download_url": request.build_absolute_uri(
                    document.edited_file.url
                ),
            },
            status=status.HTTP_200_OK,
        )