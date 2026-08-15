"""
PDF -> PDF/A. Real conversion via Ghostscript's PDF/A device (not a
metadata-only relabel) - genuinely reprocesses the file to target PDF/A
compatibility (embedded fonts, RGB color conversion, XMP metadata).

Supported target: PDF/A-1b, PDF/A-2b, or PDF/A-3b (the "basic" conformance
levels - visual reproducibility, which is what Ghostscript's built-in
converter targets; it does not attempt the stricter "a"/accessibility
conformance levels, which require semantic tagging Ghostscript doesn't
produce).

Known limitation: this verifies the output is a well-formed, openable PDF
with the source's page count preserved, and that Ghostscript accepted the
conversion. It does NOT run a formal PDF/A conformance validator (e.g.
veraPDF) - that's a separate, heavier tool this project doesn't bundle.
Document this explicitly rather than claiming certified PDF/A compliance.
"""

import os
import shutil
import subprocess
import tempfile
import uuid

import fitz

GS_TIMEOUT_SECONDS = 120
PDFA_LEVEL_TO_GS_FLAG = {"1b": "1", "2b": "2", "3b": "3"}


class PdfToPdfAError(Exception):
    """A user-facing, 400/500-worthy error: bad level, missing Ghostscript,
    or Ghostscript itself rejecting the conversion."""


def validate_level(level):
    if level not in PDFA_LEVEL_TO_GS_FLAG:
        raise PdfToPdfAError(f"level must be one of {sorted(PDFA_LEVEL_TO_GS_FLAG)} (got {level!r}).")


def _require_ghostscript():
    if shutil.which("gs") is None:
        raise PdfToPdfAError(
            "PDF/A conversion requires Ghostscript ('gs'), which is not installed on this server."
        )


def convert(file_bytes, level="2b"):
    """Returns (output_pdf_bytes, metadata dict)."""
    validate_level(level)
    _require_ghostscript()

    original_doc = fitz.open(stream=file_bytes, filetype="pdf")
    try:
        original_page_count = len(original_doc)
    finally:
        original_doc.close()

    with tempfile.TemporaryDirectory(prefix=f"pdfa-{uuid.uuid4().hex}-") as tmp_dir:
        input_path = os.path.join(tmp_dir, "input.pdf")
        output_path = os.path.join(tmp_dir, "output.pdf")
        with open(input_path, "wb") as f:
            f.write(file_bytes)

        gs_level = PDFA_LEVEL_TO_GS_FLAG[level]
        command = [
            "gs",
            f"-dPDFA={gs_level}",
            "-dBATCH", "-dNOPAUSE", "-dNOOUTERSAVE", "-dNOSAFER",
            "-sColorConversionStrategy=RGB",
            "-sDEVICE=pdfwrite",
            "-dPDFACompatibilityPolicy=1",
            f"-sOutputFile={output_path}",
            input_path,
        ]

        try:
            result = subprocess.run(
                command, capture_output=True, timeout=GS_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise PdfToPdfAError("PDF/A conversion timed out.")

        if result.returncode != 0 or not os.path.exists(output_path):
            stderr = result.stderr.decode("utf-8", errors="ignore")[:500]
            raise PdfToPdfAError(f"Ghostscript PDF/A conversion failed: {stderr}")

        with open(output_path, "rb") as f:
            output_bytes = f.read()

    converted_doc = fitz.open(stream=output_bytes, filetype="pdf")
    try:
        converted_page_count = len(converted_doc)
    finally:
        converted_doc.close()

    if converted_page_count != original_page_count:
        raise PdfToPdfAError(
            f"PDF/A output has {converted_page_count} pages, expected {original_page_count}."
        )

    metadata = {
        "page_count": converted_page_count,
        "pdfa_standard": f"PDF/A-{level.upper()}",
    }
    return output_bytes, metadata
