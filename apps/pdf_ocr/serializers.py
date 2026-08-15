from rest_framework import serializers

from apps.common.validation import validate_pdf_file

from .services import OcrError, validate_language


class OcrRequestSerializer(serializers.Serializer):
    """
    file:     a single PDF.
    language: optional, one or more Tesseract language codes joined with
              "+" (e.g. "eng" or "eng+fra"), default "eng". See
              apps.pdf_ocr.services.SUPPORTED_LANGUAGES for the installed set.
    """

    file = serializers.FileField()
    language = serializers.CharField(default="eng")

    def validate_file(self, value):
        validate_pdf_file(value)
        return value

    def validate_language(self, value):
        try:
            validate_language(value)
        except OcrError as exc:
            raise serializers.ValidationError(str(exc))
        return value
