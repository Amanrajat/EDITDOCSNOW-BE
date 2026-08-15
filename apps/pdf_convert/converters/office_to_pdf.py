"""
Word/Excel/PowerPoint -> PDF, via LibreOffice headless (`soffice
--convert-to pdf`). Real document rendering is the only production-viable
option here - there is no pure-Python library that faithfully renders
arbitrary .docx/.xlsx/.pptx layouts, fonts, and page setup the way a real
office suite does, and faking it (e.g. dumping text into a blank PDF)
would silently destroy formatting. Requires LibreOffice installed on the
server (see Dockerfile) - if it's missing, this raises a clear,
actionable error rather than pretending to succeed.

Each conversion gets its own throwaway LibreOffice user-profile directory
(`-env:UserInstallation`) so concurrent requests don't fight over the
same profile lock (a well-known failure mode when running `soffice`
headless in a multi-request server).
"""

import os
import shutil
import subprocess
import tempfile
import uuid

import fitz

SOFFICE_TIMEOUT_SECONDS = 180

EXTENSION_BY_OPERATION = {
    "word_to_pdf": "docx",
    "excel_to_pdf": "xlsx",
    "pptx_to_pdf": "pptx",
}


class OfficeToPdfError(Exception):
    """A user-facing, 400/500-worthy error: missing LibreOffice, or
    LibreOffice itself rejecting/failing the conversion."""


def _require_libreoffice():
    if shutil.which("soffice") is None:
        raise OfficeToPdfError(
            "This conversion requires LibreOffice ('soffice'), which is not installed on this server."
        )


def convert(file_bytes, source_extension):
    """Returns (pdf_bytes, metadata dict). `source_extension` is one of
    "docx", "xlsx", "pptx"."""
    _require_libreoffice()

    with tempfile.TemporaryDirectory(prefix=f"office2pdf-{uuid.uuid4().hex}-") as tmp_dir:
        input_path = os.path.join(tmp_dir, f"input.{source_extension}")
        profile_dir = os.path.join(tmp_dir, "profile")
        with open(input_path, "wb") as f:
            f.write(file_bytes)

        command = [
            "soffice", "--headless", "--norestore", "--nolockcheck", "--nodefault",
            f"-env:UserInstallation=file://{profile_dir}",
            "--convert-to", "pdf",
            "--outdir", tmp_dir,
            input_path,
        ]

        try:
            result = subprocess.run(
                command, capture_output=True, timeout=SOFFICE_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            raise OfficeToPdfError("Conversion timed out.")

        output_path = os.path.join(tmp_dir, "input.pdf")
        if result.returncode != 0 or not os.path.exists(output_path):
            stderr = result.stderr.decode("utf-8", errors="ignore")[:500]
            raise OfficeToPdfError(f"LibreOffice conversion failed: {stderr}")

        with open(output_path, "rb") as f:
            output_bytes = f.read()

    doc = fitz.open(stream=output_bytes, filetype="pdf")
    try:
        page_count = len(doc)
    finally:
        doc.close()

    if page_count == 0:
        raise OfficeToPdfError("Conversion produced an empty PDF.")

    metadata = {"page_count": page_count}
    return output_bytes, metadata
