import fitz
from rest_framework import serializers

from apps.common.validation import validate_pdf_file

from .services import RotateError, validate_degrees, validate_pages


class RotatePDFRequestSerializer(serializers.Serializer):
    """
    file:    a single PDF.
    pages:   optional, 1-based page numbers to rotate, repeated multipart
             fields (e.g. pages=1&pages=3). Omit entirely to rotate every
             page ("Rotate all").
    degrees: required, a non-zero multiple of 90. Positive = clockwise,
             negative = counter-clockwise (e.g. -90 for "rotate left").
    """

    file = serializers.FileField()
    pages = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_empty=False,
    )
    degrees = serializers.IntegerField()

    def validate_file(self, value):
        validate_pdf_file(value)
        return value

    def validate(self, attrs):
        attrs["file"].seek(0)
        with fitz.open(stream=attrs["file"].read(), filetype="pdf") as doc:
            page_count = len(doc)
        attrs["file"].seek(0)

        try:
            validate_degrees(attrs["degrees"])
        except RotateError as exc:
            raise serializers.ValidationError({"degrees": str(exc)})

        try:
            validate_pages(attrs.get("pages"), page_count)
        except RotateError as exc:
            raise serializers.ValidationError({"pages": str(exc)})

        attrs["page_count"] = page_count
        return attrs
