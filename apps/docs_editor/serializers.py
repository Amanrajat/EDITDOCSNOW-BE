from django.urls import reverse

from rest_framework import serializers

from apps.common.validation import validate_image_file

from .models import (
    Document,
    DocumentBlock,
    DocumentObject,
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

class DocumentObjectSerializer(serializers.ModelSerializer):
    """Read/output shape for a DocumentObject - see the model's docstring
    for what each field means for which object_type."""

    image_url = serializers.SerializerMethodField()

    class Meta:
        model = DocumentObject
        fields = (
            "id", "page_number", "object_type", "z_index",
            "bbox", "points", "rotation", "opacity",
            "fill_color", "stroke_color", "stroke_width",
            "text_content", "font_family", "font_size", "is_bold", "is_italic", "text_align",
            "image_url",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "image_url", "created_at", "updated_at")

    def get_image_url(self, instance):
        # instance.image_file.url is NOT servable - private_job_storage's
        # files live under BASE_DIR/private_media, outside MEDIA_ROOT, but
        # FileSystemStorage.url() has no way to know that and falls back to
        # MEDIA_URL, producing a path that looks plausible but 404s. Every
        # image is instead served through the token-gated
        # DocumentObjectImageView, exactly like edited_file goes through
        # DocumentDownloadView rather than a raw FileField URL.
        if not instance.image_file:
            return None
        request = self.context.get("request")
        path = reverse(
            "docs_editor:document-object-image",
            args=[instance.document_id, instance.id],
        )
        token = request.query_params.get("token") if request else None
        url = f"{path}?token={token}" if token else path
        return request.build_absolute_uri(url) if request else url


class DocumentObjectWriteSerializer(serializers.ModelSerializer):
    """
    Shared create/update shape. `image` is a write-only multipart upload,
    only meaningful (and required, on create) for object_type="image" -
    validated via the same shared validate_image_file() every other
    image-accepting feature (JPG-to-PDF) uses, not DRF's own ImageField.
    """

    image = serializers.FileField(required=False, write_only=True)

    class Meta:
        model = DocumentObject
        fields = (
            "page_number", "object_type", "z_index",
            "bbox", "points", "rotation", "opacity",
            "fill_color", "stroke_color", "stroke_width",
            "text_content", "font_family", "font_size", "is_bold", "is_italic", "text_align",
            "image",
        )

    def validate_image(self, value):
        validate_image_file(value)
        return value

    def validate(self, attrs):
        # On create, object_type is required and drives which other
        # fields are required. On update (partial=True), object_type is
        # immutable (not part of a PATCH payload in practice), so fall
        # back to the existing instance's type.
        object_type = attrs.get("object_type") or (self.instance.object_type if self.instance else None)
        if object_type is None:
            raise serializers.ValidationError({"object_type": "is required."})

        if object_type == "path":
            points = attrs.get("points", self.instance.points if self.instance else None)
            if not points or len(points) < 2:
                raise serializers.ValidationError({"points": "a path needs at least 2 points."})
        else:
            bbox = attrs.get("bbox", self.instance.bbox if self.instance else None)
            if not bbox or len(bbox) != 4:
                raise serializers.ValidationError({"bbox": "must be [x0, y0, x1, y1]."})
            if bbox[2] <= bbox[0] or bbox[3] <= bbox[1]:
                raise serializers.ValidationError({"bbox": "must have positive width and height."})

        if object_type == "text":
            text_content = attrs.get("text_content", self.instance.text_content if self.instance else "")
            if not text_content:
                raise serializers.ValidationError({"text_content": "a text object needs text_content."})

        if object_type == "image" and not self.instance and not attrs.get("image"):
            raise serializers.ValidationError({"image": "an image object needs an uploaded image."})

        opacity = attrs.get("opacity")
        if opacity is not None and not (0 <= opacity <= 1):
            raise serializers.ValidationError({"opacity": "must be between 0 and 1."})

        return attrs

    def create(self, validated_data):
        image = validated_data.pop("image", None)
        instance = DocumentObject.objects.create(document=self.context["document"], **validated_data)
        if image:
            ext = image.name.rsplit(".", 1)[-1].lower() if "." in image.name else "png"
            instance.image_file.save(f"{instance.id}.{ext}", image, save=True)
        return instance

    def update(self, instance, validated_data):
        image = validated_data.pop("image", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        if image:
            ext = image.name.rsplit(".", 1)[-1].lower() if "." in image.name else "png"
            instance.image_file.save(f"{instance.id}.{ext}", image, save=False)
        instance.save()
        return instance
