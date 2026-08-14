import fitz
from rest_framework import serializers

from apps.common.validation import validate_pdf_file

from .services import RemovePagesError, validate_pages_to_remove


class RemovePagesRequestSerializer(serializers.Serializer):
    """
    file:  a single PDF.
    pages: 1-based page numbers to delete, sent as repeated multipart
           fields - e.g. to remove pages 2 and 4:
           pages=2&pages=4 - the same repeated-field convention Merge's
           `order`, Split's `pages`, and Organize's `order` already use.
    """

    file = serializers.FileField()
    pages = serializers.ListField(
        child=serializers.IntegerField(),
        allow_empty=False,
    )

    def validate_file(self, value):
        validate_pdf_file(value)
        return value

    def validate(self, attrs):
        attrs["file"].seek(0)
        with fitz.open(stream=attrs["file"].read(), filetype="pdf") as doc:
            page_count = len(doc)
        attrs["file"].seek(0)

        try:
            validate_pages_to_remove(attrs["pages"], page_count)
        except RemovePagesError as exc:
            raise serializers.ValidationError({"pages": str(exc)})

        attrs["page_count"] = page_count
        return attrs
