import fitz
from rest_framework import serializers

from apps.common.validation import validate_pdf_file

from .models import PageNumberJob
from .services import PageNumberError, validate_page_numbering


class PageNumberRequestSerializer(serializers.Serializer):
    """
    file:         a single PDF.
    pages:        optional, 1-based page numbers to stamp, repeated
                  multipart fields (e.g. pages=2&pages=3). Omit entirely to
                  number every page. Numbering is sequential in page order
                  starting at start_number, regardless of which pages are
                  selected (e.g. skip a cover page and still start at 1).
    start_number: first number printed, default 1.
    position:     one of PageNumberJob.Position.
    font_size:    points, 6-72, default 12.
    font_color:   hex color, default #000000.
    margin:       points from the page edge, default 28 (~0.4in).
    prefix, suffix: optional literal text around the number (e.g.
                  prefix="Page " suffix=" of 10").
    """

    file = serializers.FileField()
    pages = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_empty=False,
    )
    start_number = serializers.IntegerField(default=1)
    position = serializers.ChoiceField(choices=PageNumberJob.Position.choices, default=PageNumberJob.Position.BOTTOM_CENTER)
    font_size = serializers.IntegerField(default=12)
    font_color = serializers.CharField(default="#000000")
    margin = serializers.FloatField(default=28.0)
    # trim_whitespace=False: a trailing/leading space is often exactly the
    # point (e.g. prefix="Page " needs that space before the number).
    prefix = serializers.CharField(required=False, allow_blank=True, default="", trim_whitespace=False)
    suffix = serializers.CharField(required=False, allow_blank=True, default="", trim_whitespace=False)

    def validate_file(self, value):
        validate_pdf_file(value)
        return value

    def validate(self, attrs):
        attrs["file"].seek(0)
        with fitz.open(stream=attrs["file"].read(), filetype="pdf") as doc:
            page_count = len(doc)
        attrs["file"].seek(0)

        try:
            validate_page_numbering(
                attrs.get("pages"),
                page_count,
                attrs["position"],
                attrs["font_size"],
                attrs["font_color"],
                attrs["margin"],
                attrs["prefix"],
                attrs["suffix"],
                attrs["start_number"],
            )
        except PageNumberError as exc:
            raise serializers.ValidationError({"page_numbering": str(exc)})

        attrs["page_count"] = page_count
        return attrs
