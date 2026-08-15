import fitz
from rest_framework import serializers

from apps.common.validation import validate_pdf_file

from .services import CropError, validate_crop_rect, validate_pages


class CropPDFRequestSerializer(serializers.Serializer):
    """
    file:          a single PDF.
    pages:         optional, 1-based page numbers to crop, repeated
                   multipart fields (e.g. pages=1&pages=3). Omit entirely
                   to crop every page ("Crop all").
    x0, y0, x1, y1: required, fractions (0..1) of each target page's own
                   width/height, top-left origin, y increasing downward -
                   this is the crop rectangle the frontend's visual editor
                   produces, expressed relative to whichever page it was
                   drawn on.
    """

    file = serializers.FileField()
    pages = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_empty=False,
    )
    x0 = serializers.FloatField()
    y0 = serializers.FloatField()
    x1 = serializers.FloatField()
    y1 = serializers.FloatField()

    def validate_file(self, value):
        validate_pdf_file(value)
        return value

    def validate(self, attrs):
        attrs["file"].seek(0)
        with fitz.open(stream=attrs["file"].read(), filetype="pdf") as doc:
            page_count = len(doc)
        attrs["file"].seek(0)

        try:
            validate_crop_rect(attrs["x0"], attrs["y0"], attrs["x1"], attrs["y1"])
        except CropError as exc:
            raise serializers.ValidationError({"crop_rect": str(exc)})

        try:
            validate_pages(attrs.get("pages"), page_count)
        except CropError as exc:
            raise serializers.ValidationError({"pages": str(exc)})

        attrs["page_count"] = page_count
        return attrs
