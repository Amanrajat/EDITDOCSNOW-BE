import fitz
from rest_framework import serializers

from apps.common.validation import validate_pdf_file

from .models import SplitJob
from .services import SplitError, chunk_every_n, parse_ranges


class SplitPDFRequestSerializer(serializers.Serializer):
    """
    file:   a single PDF.
    mode:   "all_pages" | "ranges" | "every_n" | "extract"
    ranges: required for mode=ranges. e.g. "1-5,6-10,11" (commas and/or
            newlines as separators). See parse_ranges() for the exact rule
            on overlapping/duplicate ranges - they are allowed, not merged.
    n:      required for mode=every_n. Split into consecutive chunks of n
            pages each (the last chunk may be shorter).
    pages:  required for mode=extract. 1-based page numbers, in the exact
            order given (duplicates allowed), combined into ONE output PDF.
    """

    file = serializers.FileField()
    mode = serializers.ChoiceField(choices=SplitJob.Mode.choices)
    ranges = serializers.CharField(required=False, allow_blank=False)
    n = serializers.IntegerField(required=False, min_value=1)
    pages = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        allow_empty=False,
    )

    def validate_file(self, value):
        validate_pdf_file(value)
        return value

    def validate(self, attrs):
        mode = attrs["mode"]

        # Page-count-bound validation needs the actual page count, so this
        # re-opens the file (validate_file() above already proved it's a
        # real, non-corrupted PDF; this second open is cheap at the
        # enforced <=50MB size cap and keeps range/page/n checks in one
        # well-tested place: services.parse_ranges/chunk_every_n).
        attrs["file"].seek(0)
        with fitz.open(stream=attrs["file"].read(), filetype="pdf") as doc:
            total_pages = len(doc)
        attrs["file"].seek(0)

        if mode == SplitJob.Mode.RANGES:
            if not attrs.get("ranges"):
                raise serializers.ValidationError({"ranges": "This field is required for mode=ranges."})
            try:
                parse_ranges(attrs["ranges"], total_pages)
            except SplitError as exc:
                raise serializers.ValidationError({"ranges": str(exc)})

        elif mode == SplitJob.Mode.EVERY_N:
            if not attrs.get("n"):
                raise serializers.ValidationError({"n": "This field is required for mode=every_n."})
            try:
                chunk_every_n(total_pages, attrs["n"])
            except SplitError as exc:
                raise serializers.ValidationError({"n": str(exc)})

        elif mode == SplitJob.Mode.EXTRACT:
            pages = attrs.get("pages")
            if not pages:
                raise serializers.ValidationError({"pages": "This field is required for mode=extract."})
            out_of_range = [p for p in pages if p > total_pages]
            if out_of_range:
                raise serializers.ValidationError({
                    "pages": f"Page(s) {out_of_range} are beyond the document's {total_pages} page(s).",
                })

        attrs["total_pages"] = total_pages
        return attrs
