from rest_framework import serializers

from .models import (
    Document,
    DocumentBlock
)


class DocumentBlockSerializer(serializers.ModelSerializer):

    class Meta:
        model = DocumentBlock
        fields = (
            "id",
            "page_number",
            "text",
            "bbox",
            "font_name",
            "font_size",
            "color",
            "is_bold",
            "is_italic",
            "has_link",
        )

        read_only_fields = ("id",)


class DocumentSerializer(serializers.ModelSerializer):

    blocks = DocumentBlockSerializer(
        many=True,
        read_only=True
    )

    class Meta:
        model = Document

        fields = (
            "id",
            "user",
            "original_file",
            "edited_file",
            "original_name",
            "file_type",
            "file_size",
            "total_pages",
            "status",
            "error_message",
            "created_at",
            "updated_at",
            "blocks",
        )

        read_only_fields = (
            "id",
            "user",
            "edited_file",
            "file_size",
            "total_pages",
            "status",
            "error_message",
            "created_at",
            "updated_at",
        )


class DocumentUploadSerializer(serializers.ModelSerializer):

    class Meta:
        model = Document
        fields = (
            "original_file",
        )

    def validate_original_file(self, value):

        max_size = 20 * 1024 * 1024  # 20 MB

        if value.size > max_size:
            raise serializers.ValidationError(
                "File size cannot exceed 20 MB."
            )

        filename = value.name.lower()

        if not filename.endswith(".pdf"):
            raise serializers.ValidationError(
                "Only PDF files are allowed."
            )

        content_type = getattr(
            value,
            "content_type",
            None
        )

        if content_type and content_type != "application/pdf":
            raise serializers.ValidationError(
                "Invalid PDF file."
            )

        return value


class DocumentBlockUpdateSerializer(
    serializers.Serializer
):

    id = serializers.UUIDField()

    text = serializers.CharField(
        required=True,
        allow_blank=False,
        max_length=10000,
        trim_whitespace=False,
    )


class SaveEditedBlocksSerializer(
    serializers.Serializer
):

    blocks = DocumentBlockUpdateSerializer(
        many=True,
        required=True
    )

    def validate_blocks(self, value):

        if not value:
            raise serializers.ValidationError(
                "At least one block is required."
            )

        return value