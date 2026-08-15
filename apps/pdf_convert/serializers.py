import fitz
from rest_framework import serializers

from apps.common.validation import validate_image_file, validate_office_file, validate_pdf_file

from .converters.html_to_pdf import HtmlToPdfError
from .converters.html_to_pdf import validate_options as validate_html_to_pdf_options
from .converters.jpg_to_pdf import JpgToPdfError
from .converters.jpg_to_pdf import validate_options as validate_jpg_to_pdf_options
from .converters.pdf_to_jpg import (
    DEFAULT_DPI,
    DEFAULT_QUALITY,
    PdfToJpgError,
    validate_dpi,
    validate_pages,
    validate_quality,
)
from .converters.pdf_to_pdfa import PdfToPdfAError
from .converters.pdf_to_pdfa import validate_level as validate_pdfa_level


class SourcePdfRequestSerializer(serializers.Serializer):
    """Shared request shape for every PDF-to-X conversion that just needs
    the source PDF and nothing else (Word/Excel/PowerPoint/Markdown)."""

    file = serializers.FileField()

    def validate_file(self, value):
        validate_pdf_file(value)
        return value


class PdfToJpgRequestSerializer(serializers.Serializer):
    """
    file:    a single PDF.
    pages:   optional, 1-based page numbers to render, repeated multipart
             fields (e.g. pages=1&pages=3). Omit entirely to render every
             page ("Convert all").
    dpi:     optional, render resolution, default 150.
    quality: optional, JPEG quality 1-100, default 90.
    """

    file = serializers.FileField()
    pages = serializers.ListField(
        child=serializers.IntegerField(),
        required=False,
        allow_empty=False,
    )
    dpi = serializers.IntegerField(default=DEFAULT_DPI)
    quality = serializers.IntegerField(default=DEFAULT_QUALITY)

    def validate_file(self, value):
        validate_pdf_file(value)
        return value

    def validate(self, attrs):
        attrs["file"].seek(0)
        with fitz.open(stream=attrs["file"].read(), filetype="pdf") as doc:
            page_count = len(doc)
        attrs["file"].seek(0)

        try:
            validate_pages(attrs.get("pages"), page_count)
            validate_dpi(attrs["dpi"])
            validate_quality(attrs["quality"])
        except PdfToJpgError as exc:
            raise serializers.ValidationError({"pdf_to_jpg": str(exc)})

        return attrs


MAX_JPG_TO_PDF_FILES = 50


class JpgToPdfRequestSerializer(serializers.Serializer):
    """
    files:       one or more JPG/PNG images, repeated multipart fields
                 (e.g. files=a.jpg&files=b.jpg) - page order follows this
                 order unless `order` is given.
    order:       optional 0-based permutation of `files`' indices, so a
                 frontend can let the user drag-and-drop reorder without
                 re-uploading (same convention as Merge PDF's `order`).
    page_size:   "A4" (default) or "Letter".
    orientation: "portrait" (default) or "landscape".
    fit_mode:    "fit" (default, contain - whole image visible) or "fill"
                 (cover - cropped to fill the page with no letterboxing).
    margin:      points around each image, default 0.
    quality:     optional JPEG re-encode quality 1-100, only used when
                 fit_mode="fill" (the crop step re-encodes); "fit" embeds
                 the original bytes untouched.
    """

    files = serializers.ListField(
        child=serializers.FileField(),
        allow_empty=False,
    )
    order = serializers.ListField(
        child=serializers.IntegerField(min_value=0),
        required=False,
        allow_empty=False,
    )
    page_size = serializers.ChoiceField(choices=["A4", "Letter"], default="A4")
    orientation = serializers.ChoiceField(choices=["portrait", "landscape"], default="portrait")
    fit_mode = serializers.ChoiceField(choices=["fit", "fill"], default="fit")
    margin = serializers.FloatField(default=0)
    quality = serializers.IntegerField(required=False, allow_null=True, default=None)

    def validate_files(self, value):
        if len(value) > MAX_JPG_TO_PDF_FILES:
            raise serializers.ValidationError(
                f"At most {MAX_JPG_TO_PDF_FILES} images can be converted at once."
            )
        for image in value:
            validate_image_file(image)
        return value

    def validate(self, attrs):
        files = attrs["files"]
        order = attrs.get("order")

        if order is not None and sorted(order) != list(range(len(files))):
            raise serializers.ValidationError({
                "order": f"must be a permutation of file indices 0..{len(files) - 1}.",
            })

        try:
            validate_jpg_to_pdf_options(
                attrs["page_size"], attrs["orientation"], attrs["fit_mode"], attrs["margin"], attrs["quality"],
            )
        except JpgToPdfError as exc:
            raise serializers.ValidationError({"jpg_to_pdf": str(exc)})

        return attrs


class PdfToPdfARequestSerializer(serializers.Serializer):
    """
    file:  a single PDF.
    level: one of "1b", "2b" (default), "3b" - see pdf_to_pdfa.py's
           module docstring for exactly what these target.
    """

    file = serializers.FileField()
    level = serializers.ChoiceField(choices=["1b", "2b", "3b"], default="2b")

    def validate_file(self, value):
        validate_pdf_file(value)
        return value

    def validate(self, attrs):
        try:
            validate_pdfa_level(attrs["level"])
        except PdfToPdfAError as exc:
            raise serializers.ValidationError({"level": str(exc)})
        return attrs


class WordToPdfRequestSerializer(serializers.Serializer):
    """file: a single .docx."""

    file = serializers.FileField()

    def validate_file(self, value):
        validate_office_file(value, "docx")
        return value


class ExcelToPdfRequestSerializer(serializers.Serializer):
    """file: a single .xlsx."""

    file = serializers.FileField()

    def validate_file(self, value):
        validate_office_file(value, "xlsx")
        return value


class PptxToPdfRequestSerializer(serializers.Serializer):
    """file: a single .pptx."""

    file = serializers.FileField()

    def validate_file(self, value):
        validate_office_file(value, "pptx")
        return value


class HtmlToPdfRequestSerializer(serializers.Serializer):
    """
    Exactly one of `url` / `html` is required.
    url:         an http(s) URL to fetch and render (SSRF-validated -
                 see converters/html_to_pdf.py).
    html:        a raw HTML string to render directly (no fetch of the
                 top-level document - any resources it references are
                 still SSRF-validated when WeasyPrint loads them).
    page_size:   "A4" (default) or "Letter".
    orientation: "portrait" (default) or "landscape".
    """

    url = serializers.URLField(required=False, allow_blank=False)
    html = serializers.CharField(required=False, allow_blank=False, trim_whitespace=False)
    page_size = serializers.ChoiceField(choices=["A4", "Letter"], default="A4")
    orientation = serializers.ChoiceField(choices=["portrait", "landscape"], default="portrait")

    def validate(self, attrs):
        url = attrs.get("url")
        html = attrs.get("html")
        if bool(url) == bool(html):
            raise serializers.ValidationError(
                {"non_field_errors": ["Provide exactly one of 'url' or 'html'."]}
            )

        try:
            validate_html_to_pdf_options(attrs["page_size"], attrs["orientation"])
        except HtmlToPdfError as exc:
            raise serializers.ValidationError({"options": str(exc)})

        return attrs
