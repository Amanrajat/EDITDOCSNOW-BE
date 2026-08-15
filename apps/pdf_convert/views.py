from django.urls import reverse
from rest_framework.views import APIView

from apps.common.responses import error_response, success_response
from apps.common.views import OwnedJobDownloadView

from .converters import (
    html_to_pdf,
    jpg_to_pdf,
    office_to_pdf,
    pdf_to_excel,
    pdf_to_jpg,
    pdf_to_markdown,
    pdf_to_pdfa,
    pdf_to_pptx,
    pdf_to_word,
)
from .models import ConversionJob
from .serializers import (
    ExcelToPdfRequestSerializer,
    HtmlToPdfRequestSerializer,
    JpgToPdfRequestSerializer,
    PdfToJpgRequestSerializer,
    PdfToPdfARequestSerializer,
    PptxToPdfRequestSerializer,
    SourcePdfRequestSerializer,
    WordToPdfRequestSerializer,
)
from .services import run_conversion, run_conversion_multi, run_conversion_no_file


def _job_response(request, job, message):
    if job.status == ConversionJob.Status.FAILED:
        return error_response(
            "Unable to convert file.", error_code="CONVERSION_FAILED", status_code=500,
        )

    download_path = reverse("pdf_convert:convert-download", args=[job.id])
    download_url = request.build_absolute_uri(f"{download_path}?token={job.owner_token}")

    return success_response(
        message,
        data={
            "file_id": str(job.id),
            "owner_token": job.owner_token,
            "download_url": download_url,
            "filename": job.get_filename(),
            "operation": job.operation,
            **job.metadata,
        },
        status_code=201,
    )


class PdfToWordView(APIView):
    """
    POST /api/v1/pdf/convert/pdf-to-word/

    multipart/form-data:
        file: a single PDF

    Converts to a real .docx - text (bold/italic preserved), tables,
    embedded images, and page breaks. A page with no extractable text
    (scanned/image-only) is embedded as a full-page image instead of
    silently dropped; see `metadata.scanned_pages` in the response.
    """

    def post(self, request):
        serializer = SourcePdfRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "Invalid request.", error_code="VALIDATION_ERROR", status_code=400,
                errors=serializer.errors,
            )

        user = request.user if request.user.is_authenticated else None
        job = run_conversion(
            user=user,
            uploaded_file=serializer.validated_data["file"],
            operation=ConversionJob.Operation.PDF_TO_WORD,
            converter_fn=pdf_to_word.convert,
            output_ext="docx",
        )
        return _job_response(request, job, "PDF converted to Word successfully")


class PdfToExcelView(APIView):
    """
    POST /api/v1/pdf/convert/pdf-to-excel/

    multipart/form-data:
        file: a single PDF

    Real table extraction (PyMuPDF's find_tables) into a genuine .xlsx,
    one sheet per source page. A page with no detected table still gets
    its plain text written line-by-line rather than a blank sheet.
    """

    def post(self, request):
        serializer = SourcePdfRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "Invalid request.", error_code="VALIDATION_ERROR", status_code=400,
                errors=serializer.errors,
            )

        user = request.user if request.user.is_authenticated else None
        job = run_conversion(
            user=user,
            uploaded_file=serializer.validated_data["file"],
            operation=ConversionJob.Operation.PDF_TO_EXCEL,
            converter_fn=pdf_to_excel.convert,
            output_ext="xlsx",
        )
        return _job_response(request, job, "PDF converted to Excel successfully")


class PdfToPptxView(APIView):
    """
    POST /api/v1/pdf/convert/pdf-to-pptx/

    multipart/form-data:
        file: a single PDF

    One slide per page, reconstructed from real content - editable text
    boxes, real tables, and images placed at their actual position (not a
    flattened screenshot). A scanned/image-only page falls back to a
    full-slide image; see `metadata.scanned_pages`.
    """

    def post(self, request):
        serializer = SourcePdfRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "Invalid request.", error_code="VALIDATION_ERROR", status_code=400,
                errors=serializer.errors,
            )

        user = request.user if request.user.is_authenticated else None
        job = run_conversion(
            user=user,
            uploaded_file=serializer.validated_data["file"],
            operation=ConversionJob.Operation.PDF_TO_PPTX,
            converter_fn=pdf_to_pptx.convert,
            output_ext="pptx",
        )
        return _job_response(request, job, "PDF converted to PowerPoint successfully")


class PdfToJpgView(APIView):
    """
    POST /api/v1/pdf/convert/pdf-to-jpg/

    multipart/form-data:
        file:    a single PDF
        pages:   optional, 1-based page numbers to render, repeated
                 fields (e.g. pages=1&pages=3) - omit to render every page
        dpi:     optional, render resolution, default 150
        quality: optional, JPEG quality 1-100, default 90

    Real page rasterization - a single requested page returns one .jpg,
    more than one is zipped.
    """

    def post(self, request):
        serializer = PdfToJpgRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "Invalid request.", error_code="VALIDATION_ERROR", status_code=400,
                errors=serializer.errors,
            )

        data = serializer.validated_data
        user = request.user if request.user.is_authenticated else None
        job = run_conversion(
            user=user,
            uploaded_file=data["file"],
            operation=ConversionJob.Operation.PDF_TO_JPG,
            converter_fn=lambda file_bytes: pdf_to_jpg.convert(
                file_bytes, pages=data.get("pages"), dpi=data["dpi"], quality=data["quality"],
            ),
            output_ext="jpg",
        )
        return _job_response(request, job, "PDF converted to JPG successfully")


class PdfToMarkdownView(APIView):
    """
    POST /api/v1/pdf/convert/pdf-to-markdown/

    multipart/form-data:
        file: a single PDF

    Extracts real structure - headings (by relative font size), paragraphs,
    lists, bold/italic emphasis, tables, and links - into a genuine .md
    file, not flat unstructured text.
    """

    def post(self, request):
        serializer = SourcePdfRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "Invalid request.", error_code="VALIDATION_ERROR", status_code=400,
                errors=serializer.errors,
            )

        user = request.user if request.user.is_authenticated else None
        job = run_conversion(
            user=user,
            uploaded_file=serializer.validated_data["file"],
            operation=ConversionJob.Operation.PDF_TO_MARKDOWN,
            converter_fn=pdf_to_markdown.convert,
            output_ext="md",
        )
        return _job_response(request, job, "PDF converted to Markdown successfully")


class JpgToPdfView(APIView):
    """
    POST /api/v1/pdf/convert/jpg-to-pdf/

    multipart/form-data:
        files:       one or more JPG/PNG images, repeated fields
                     (e.g. files=a.jpg&files=b.jpg)
        order:       optional 0-based permutation of `files`' indices
        page_size:   optional, "A4" (default) or "Letter"
        orientation: optional, "portrait" (default) or "landscape"
        fit_mode:    optional, "fit" (default) or "fill"
        margin:      optional, points, default 0
        quality:     optional, JPEG quality 1-100 (only affects "fill"'s
                     crop re-encode)

    One real PDF page per image, in the given order.
    """

    def post(self, request):
        serializer = JpgToPdfRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "Invalid request.", error_code="VALIDATION_ERROR", status_code=400,
                errors=serializer.errors,
            )

        data = serializer.validated_data
        files = data["files"]
        order = data.get("order") or list(range(len(files)))
        ordered_files = [files[i] for i in order]

        def build():
            images = []
            for uploaded in ordered_files:
                uploaded.seek(0)
                images.append((uploaded.name, uploaded.read()))
            return jpg_to_pdf.convert(
                images,
                page_size=data["page_size"],
                orientation=data["orientation"],
                fit_mode=data["fit_mode"],
                margin=data["margin"],
                quality=data["quality"],
            )

        user = request.user if request.user.is_authenticated else None
        job = run_conversion_multi(
            user=user,
            uploaded_files=ordered_files,
            operation=ConversionJob.Operation.JPG_TO_PDF,
            converter_fn=build,
            output_ext="pdf",
        )
        return _job_response(request, job, "Images converted to PDF successfully")


class PdfToPdfAView(APIView):
    """
    POST /api/v1/pdf/convert/pdf-to-pdfa/

    multipart/form-data:
        file:  a single PDF
        level: optional, one of "1b", "2b" (default), "3b"

    Real Ghostscript-based PDF/A conversion. See converters/pdf_to_pdfa.py
    for exactly what's verified vs. not (no bundled formal PDF/A validator).
    """

    def post(self, request):
        serializer = PdfToPdfARequestSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "Invalid request.", error_code="VALIDATION_ERROR", status_code=400,
                errors=serializer.errors,
            )

        data = serializer.validated_data
        user = request.user if request.user.is_authenticated else None
        job = run_conversion(
            user=user,
            uploaded_file=data["file"],
            operation=ConversionJob.Operation.PDF_TO_PDFA,
            converter_fn=lambda file_bytes: pdf_to_pdfa.convert(file_bytes, level=data["level"]),
            output_ext="pdf",
        )
        return _job_response(request, job, "PDF converted to PDF/A successfully")


class WordToPdfView(APIView):
    """
    POST /api/v1/pdf/convert/word-to-pdf/

    multipart/form-data:
        file: a single .docx

    Real LibreOffice-based rendering (requires 'soffice' on the server -
    see converters/office_to_pdf.py and the Dockerfile).
    """

    def post(self, request):
        serializer = WordToPdfRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "Invalid request.", error_code="VALIDATION_ERROR", status_code=400,
                errors=serializer.errors,
            )

        user = request.user if request.user.is_authenticated else None
        job = run_conversion(
            user=user,
            uploaded_file=serializer.validated_data["file"],
            operation=ConversionJob.Operation.WORD_TO_PDF,
            converter_fn=lambda file_bytes: office_to_pdf.convert(file_bytes, "docx"),
            output_ext="pdf",
        )
        return _job_response(request, job, "Word document converted to PDF successfully")


class ExcelToPdfView(APIView):
    """
    POST /api/v1/pdf/convert/excel-to-pdf/

    multipart/form-data:
        file: a single .xlsx

    Real LibreOffice-based rendering (requires 'soffice' on the server).
    """

    def post(self, request):
        serializer = ExcelToPdfRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "Invalid request.", error_code="VALIDATION_ERROR", status_code=400,
                errors=serializer.errors,
            )

        user = request.user if request.user.is_authenticated else None
        job = run_conversion(
            user=user,
            uploaded_file=serializer.validated_data["file"],
            operation=ConversionJob.Operation.EXCEL_TO_PDF,
            converter_fn=lambda file_bytes: office_to_pdf.convert(file_bytes, "xlsx"),
            output_ext="pdf",
        )
        return _job_response(request, job, "Excel workbook converted to PDF successfully")


class PptxToPdfView(APIView):
    """
    POST /api/v1/pdf/convert/pptx-to-pdf/

    multipart/form-data:
        file: a single .pptx

    Real LibreOffice-based rendering (requires 'soffice' on the server).
    """

    def post(self, request):
        serializer = PptxToPdfRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "Invalid request.", error_code="VALIDATION_ERROR", status_code=400,
                errors=serializer.errors,
            )

        user = request.user if request.user.is_authenticated else None
        job = run_conversion(
            user=user,
            uploaded_file=serializer.validated_data["file"],
            operation=ConversionJob.Operation.PPTX_TO_PDF,
            converter_fn=lambda file_bytes: office_to_pdf.convert(file_bytes, "pptx"),
            output_ext="pdf",
        )
        return _job_response(request, job, "Presentation converted to PDF successfully")


class HtmlToPdfView(APIView):
    """
    POST /api/v1/pdf/convert/html-to-pdf/

    multipart/form-data (or JSON):
        url:         an http(s) URL to render (SSRF-validated - private/
                     loopback/link-local/reserved IPs, non-default ports,
                     and non-http(s) schemes are all blocked; see
                     converters/html_to_pdf.py)
        html:        OR a raw HTML string to render directly
        page_size:   optional, "A4" (default) or "Letter"
        orientation: optional, "portrait" (default) or "landscape"

    Exactly one of url/html is required.
    """

    def post(self, request):
        serializer = HtmlToPdfRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return error_response(
                "Invalid request.", error_code="VALIDATION_ERROR", status_code=400,
                errors=serializer.errors,
            )

        data = serializer.validated_data
        url = data.get("url")
        html = data.get("html")

        try:
            html_to_pdf.validate_url(url) if url else None
        except html_to_pdf.HtmlToPdfError as exc:
            return error_response(str(exc), error_code="BLOCKED_URL", status_code=400)

        user = request.user if request.user.is_authenticated else None
        job = run_conversion_no_file(
            user=user,
            source_description=(url or "inline HTML"),
            operation=ConversionJob.Operation.HTML_TO_PDF,
            converter_fn=lambda: html_to_pdf.convert(
                url=url, html=html, page_size=data["page_size"], orientation=data["orientation"],
            ),
            output_ext="pdf",
        )
        return _job_response(request, job, "HTML converted to PDF successfully")


class ConversionJobDownloadView(OwnedJobDownloadView):
    """
    GET /api/v1/pdf/convert/<job_id>/download/?token=<owner_token>

    Shared download endpoint for every conversion operation - filename
    and content-type are derived from the job's own operation/output_is_zip
    (see ConversionJob.get_filename/get_content_type), so one view/URL
    serves PDF-to-Word, PDF-to-Excel, PDF-to-JPG (zip), etc. alike.
    """

    model = ConversionJob

    def get_filename(self, job):
        return job.get_filename()

    def get_content_type(self, job):
        return job.get_content_type()
