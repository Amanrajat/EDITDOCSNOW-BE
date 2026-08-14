import fitz
from rest_framework import serializers

from apps.common.validation import validate_pdf_file

from .services import OrganizeError, validate_order


class OrganizePDFRequestSerializer(serializers.Serializer):
    """
    file:  a single PDF.
    order: the full new page order, 1-based (page 1 is what the user sees
           as "page 1"), sent as repeated multipart fields - e.g. for a
           5-page document reordered to [3,1,5,2,4]:
           order=3&order=1&order=5&order=2&order=4 - the same convention
           Merge's `order` and Split's `pages` already use.
    """

    file = serializers.FileField()
    order = serializers.ListField(
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
            validate_order(attrs["order"], page_count)
        except OrganizeError as exc:
            raise serializers.ValidationError({"order": str(exc)})

        attrs["page_count"] = page_count
        return attrs
