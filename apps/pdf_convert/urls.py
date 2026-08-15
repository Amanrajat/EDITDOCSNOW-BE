from django.urls import path

from .views import (
    ConversionJobDownloadView,
    ExcelToPdfView,
    HtmlToPdfView,
    JpgToPdfView,
    PdfToExcelView,
    PdfToJpgView,
    PdfToMarkdownView,
    PdfToPdfAView,
    PdfToPptxView,
    PdfToWordView,
    PptxToPdfView,
    WordToPdfView,
)

app_name = "pdf_convert"

urlpatterns = [
    path("convert/pdf-to-word/", PdfToWordView.as_view(), name="pdf-to-word"),
    path("convert/pdf-to-excel/", PdfToExcelView.as_view(), name="pdf-to-excel"),
    path("convert/pdf-to-pptx/", PdfToPptxView.as_view(), name="pdf-to-pptx"),
    path("convert/pdf-to-jpg/", PdfToJpgView.as_view(), name="pdf-to-jpg"),
    path("convert/pdf-to-markdown/", PdfToMarkdownView.as_view(), name="pdf-to-markdown"),
    path("convert/pdf-to-pdfa/", PdfToPdfAView.as_view(), name="pdf-to-pdfa"),
    path("convert/jpg-to-pdf/", JpgToPdfView.as_view(), name="jpg-to-pdf"),
    path("convert/word-to-pdf/", WordToPdfView.as_view(), name="word-to-pdf"),
    path("convert/excel-to-pdf/", ExcelToPdfView.as_view(), name="excel-to-pdf"),
    path("convert/pptx-to-pdf/", PptxToPdfView.as_view(), name="pptx-to-pdf"),
    path("convert/html-to-pdf/", HtmlToPdfView.as_view(), name="html-to-pdf"),
    path("convert/<str:job_id>/download/", ConversionJobDownloadView.as_view(), name="convert-download"),
]
