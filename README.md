# EditDocsNow — Backend

Django REST API for uploading a PDF, extracting its editable text blocks, letting a
frontend edit them, and regenerating a new PDF with the edits applied.

## Tech stack

- **Django 5.2** + **Django REST Framework** — HTTP API
- **PyMuPDF (`fitz`)** — PDF text extraction and regeneration
- **PostgreSQL** — primary datastore (via `psycopg2-binary`)
- **django-cors-headers** — CORS for a separately-hosted frontend
- **python-decouple** — environment-based configuration

## Project structure

```
core/                   Django project (settings, root URLs)
apps/docs_editor/
  models.py             Document, DocumentBlock
  views.py              API views (upload, detail, extract, save)
  serializers.py        DRF serializers + upload validation
  services.py           DocumentService, BlockExtractionService, BlockUpdateService
  pdf_extractor.py       PDF -> text block extraction (PyMuPDF)
  pdf_regenerator.py     Blocks -> edited PDF (redact + reinsert text)
  urls.py               App routes, mounted at /docs_editor/
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt

copy .env.example .env            # then fill in real values
python manage.py migrate
python manage.py runserver
```

`.env` must define (see `.env.example`):

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Django secret key (optional in dev, required in prod) |
| `DEBUG` | `True`/`False` |
| `ALLOWED_HOSTS` | Comma-separated hosts |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | PostgreSQL connection |

The API is served under `/docs_editor/`, uploaded/generated files under `/media/`
(only served by Django itself when `DEBUG=True`).

## Docker

```bash
docker build -t editdocsnow-be .
docker run -p 8000:8000 \
  -e SECRET_KEY=change-me \
  -e DEBUG=False \
  -e ALLOWED_HOSTS=localhost \
  -e DATABASE_URL=postgres://user:password@host:5432/dbname \
  editdocsnow-be
```

The container's `entrypoint.sh` runs `migrate`, then `collectstatic`, then starts
`gunicorn`. It accepts either a single `DATABASE_URL` or the discrete `DB_*`
vars from `.env.example`.

## Deploy to Render

`render.yaml` is a Blueprint that provisions a free web service (built from
the `Dockerfile`) and a free managed Postgres database, wired together via
`DATABASE_URL`.

1. Push this repo to GitHub/GitLab.
2. In the Render dashboard: **New > Blueprint**, point it at the repo.
3. Render creates `editdocsnow-be` (web) and `editdocsnow-db` (Postgres) and
   generates `SECRET_KEY` automatically.

Known limitation: no object storage (S3, etc.) is configured, so uploaded and
generated PDFs live on the web service's local disk under `/app/media`. The
free plan has no persistent disk, so **files are lost on every deploy/restart**.
For real usage, either upgrade to a paid instance and uncomment the `disk:`
block in `render.yaml`, or swap `MEDIA` storage for `django-storages` + S3.

## API reference

Base URL: `http://localhost:8000/docs_editor/`

### 1. Upload a PDF

```
POST /upload/
Content-Type: multipart/form-data
Body: original_file=<file>
```

Constraints: PDF only, max 20 MB.

Response `201`:
```json
{
  "id": "b3c1...",
  "original_file": "/media/documents/original/sample.pdf",
  "edited_file": null,
  "original_name": "sample.pdf",
  "file_type": "pdf",
  "file_size": 48213,
  "total_pages": 3,
  "status": "uploaded",
  "error_message": "",
  "blocks": []
}
```

If the file can't be parsed as a PDF, `status` is `"failed"` and `error_message`
is populated.

### 2. Get document detail

```
GET /<document_id>/
```

Returns the same shape as upload, including any extracted `blocks`.

### 3. Extract editable blocks

```
POST /<document_id>/extract/
```

Runs text extraction and stores one `DocumentBlock` per text block found.
Re-running this deletes and re-extracts all blocks for the document.

Response `200`:
```json
{
  "document_id": "b3c1...",
  "total_blocks": 42,
  "blocks": [
    {
      "id": "9f2a...",
      "page_number": 0,
      "text": "Invoice #1024",
      "bbox": [72.0, 84.3, 210.5, 100.1],
      "font_name": "Helvetica-Bold",
      "font_size": 14.0,
      "color": "#000000",
      "is_bold": true,
      "is_italic": false,
      "has_link": false
    }
  ]
}
```

### 4. Save edited blocks

```
POST /<document_id>/save/
Content-Type: application/json
Body: { "blocks": [ { "id": "9f2a...", "text": "Invoice #2025" }, ... ] }
```

Only `id` + `text` are editable per block. The backend regenerates the PDF
from the **original** file using the current text of *every* block on the
document (not just the ones in this request), so edits made across multiple
save calls accumulate correctly.

Response `200`:
```json
{
  "document_id": "b3c1...",
  "download_url": "http://localhost:8000/media/documents/edited/edited_....pdf"
}
```

On regeneration failure, `status` is `500` and the document's `status`/`error_message`
are updated to `"failed"`.

## Frontend integration

All examples assume `API_BASE = "http://localhost:8000/docs_editor"` and that
CORS is open (`CORS_ALLOW_ALL_ORIGINS = True` in dev).

### Upload

```javascript
async function uploadDocument(file) {
  const formData = new FormData();
  formData.append("original_file", file);

  const res = await fetch(`${API_BASE}/upload/`, {
    method: "POST",
    body: formData,
  });

  if (!res.ok) {
    const err = await res.json();
    throw new Error(JSON.stringify(err));
  }

  return res.json(); // Document
}
```

### Extract blocks (after upload)

```javascript
async function extractBlocks(documentId) {
  const res = await fetch(`${API_BASE}/${documentId}/extract/`, {
    method: "POST",
  });
  if (!res.ok) throw new Error("Extraction failed");
  return res.json(); // { document_id, total_blocks, blocks }
}
```

### Render blocks for editing

Each block's `bbox` (`[x0, y0, x1, y1]`) is in PDF point coordinates. To
overlay editable text on a rendered page image, scale by
`renderedWidth / pageWidthInPoints`.

```javascript
function blockToOverlayStyle(block, scale) {
  const [x0, y0, x1, y1] = block.bbox;
  return {
    position: "absolute",
    left: x0 * scale,
    top: y0 * scale,
    width: (x1 - x0) * scale,
    height: (y1 - y0) * scale,
    fontSize: block.font_size * scale,
    fontWeight: block.is_bold ? "bold" : "normal",
    fontStyle: block.is_italic ? "italic" : "normal",
    color: block.color,
  };
}
```

### Save edits and download

```javascript
async function saveEdits(documentId, editedBlocks) {
  // editedBlocks: [{ id, text }, ...] for every block on the document
  const res = await fetch(`${API_BASE}/${documentId}/save/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ blocks: editedBlocks }),
  });

  if (!res.ok) throw new Error("Save failed");

  const { download_url } = await res.json();
  window.location.href = download_url; // or open in a new tab
}
```

### Full flow (axios variant)

```javascript
import axios from "axios";

const api = axios.create({ baseURL: "http://localhost:8000/docs_editor" });

async function editPdfFlow(file, editsById) {
  const { data: doc } = await api.post(
    "/upload/",
    (() => {
      const fd = new FormData();
      fd.append("original_file", file);
      return fd;
    })(),
    { headers: { "Content-Type": "multipart/form-data" } }
  );

  const { data: extracted } = await api.post(`/${doc.id}/extract/`);

  const blocks = extracted.blocks.map((b) => ({
    id: b.id,
    text: editsById[b.id] ?? b.text,
  }));

  const { data: saved } = await api.post(`/${doc.id}/save/`, { blocks });

  return saved.download_url;
}
```

## Notes from code review

Fixed in this pass:
- `pdf_extractor.py`: font size was always hard-coded to `12` (a truthy-default bug
  meant the real span size was never read); text color extraction was disabled and
  hard-coded to `#000000`. Both now reflect the actual PDF content.
- `views.py` / `pdf_regenerator.py`: removed leftover debug `print()` statements.
- `SaveEditedBlocksView` now regenerates from **all** of the document's blocks
  (not just the ones sent in the request), so sequential partial saves no longer
  lose earlier edits, and both upload and save now catch regeneration failures
  and persist `status="failed"` + `error_message` instead of raising a bare 500.
- `core/settings.py`: dropped unused imports (`os`, `timedelta`); `SECRET_KEY`,
  `DEBUG`, `ALLOWED_HOSTS` now come from the environment (via `python-decouple`)
  instead of being hard-coded, consistent with the DB settings.
- Added `.gitignore`, `requirements.txt`, and `.env.example`; removed
  `db.sqlite3` and compiled `__pycache__` files from version control (they were
  previously committed, and are now gitignored — the working files are untouched).

Not fixed, worth knowing:
- No authentication/permission classes are configured — every endpoint is
  effectively public. Fine for local development, not for production.
- `CORS_ALLOW_ALL_ORIGINS = True` should be replaced with an explicit
  `CORS_ALLOWED_ORIGINS` list before deploying.
- Re-running `/extract/` deletes and recreates all blocks for a document, so any
  in-progress edits not yet saved are lost — the frontend should treat extraction
  as a one-time step per document unless it also re-uploads current edits after.
- `pdf_regenerator.py` calls `apply_redactions()` once per block; correct, but
  redundant when several blocks share a page — a minor performance opportunity,
  not a correctness issue.
