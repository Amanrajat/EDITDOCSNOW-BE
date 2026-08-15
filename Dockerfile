FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# System deps:
# - libpq5      -> runtime for psycopg2-binary (Postgres client)
# - curl        -> healthchecks
# - ghostscript -> PDF -> PDF/A conversion (apps/pdf_convert/converters/pdf_to_pdfa.py
#   shells out to `gs`)
# - libreoffice-writer/-calc/-impress -> Word/Excel/PowerPoint -> PDF conversion
#   (apps/pdf_convert/converters/office_to_pdf.py shells out to `soffice`).
#   Installed as the three specific components rather than the full
#   `libreoffice` metapackage (draw/base/math etc.) to keep the image smaller,
#   since only these three conversions exist.
# - fonts-liberation/fonts-dejavu-core -> better Times/Arial/Courier-family
#   substitution so converted Office documents keep reasonably faithful
#   metrics when LibreOffice renders them without the original fonts.
# - libpango-1.0-0/libpangocairo-1.0-0/libcairo2/libgdk-pixbuf-2.0-0 ->
#   WeasyPrint (HTML -> PDF) loads Pango/cairo via cffi at import time even
#   in its newer "mostly pure-Python" releases - without these the import
#   itself fails (OSError: cannot load library 'libpango-1.0-0'), which
#   would break the whole apps.pdf_convert URL module since html_to_pdf.py
#   is imported unconditionally by its views.
# - tesseract-ocr + tesseract-ocr-<lang> -> OCR (apps/pdf_ocr, via the
#   ocrmypdf Python package). Language packs match
#   apps.pdf_ocr.services.SUPPORTED_LANGUAGES exactly - add both here and
#   there together if OCR needs to support another language.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
        ghostscript \
        libreoffice-writer \
        libreoffice-calc \
        libreoffice-impress \
        fonts-liberation \
        fonts-dejavu-core \
        libpango-1.0-0 \
        libpangocairo-1.0-0 \
        libcairo2 \
        libgdk-pixbuf-2.0-0 \
        tesseract-ocr \
        tesseract-ocr-eng \
        tesseract-ocr-fra \
        tesseract-ocr-deu \
        tesseract-ocr-spa \
        tesseract-ocr-hin \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/media /app/staticfiles /app/private_media

RUN chmod +x /app/entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/app/entrypoint.sh"]
