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
apps/common/            Shared helpers reused across PDF-processing apps
  validation.py         PDF upload validation (size, extension, magic bytes, corruption, page count)
  responses.py          Consistent {success, message, data|error_code} API envelope
apps/docs_editor/
  models.py             Document, DocumentBlock
  views.py              API views (upload, detail, extract, save)
  serializers.py        DRF serializers + upload validation
  services.py           DocumentService, BlockExtractionService, BlockUpdateService
  pdf_extractor.py       PDF -> text block extraction (PyMuPDF)
  pdf_regenerator.py     Blocks -> edited PDF (redact + reinsert text)
  urls.py               App routes, mounted at /docs_editor/
apps/pdf_merge/         Merge PDF feature, mounted at /api/v1/pdf/
  models.py             MergeJob (result tracking, UUID-addressable)
  services.py           MergePDFService (PyMuPDF insert_pdf, page-order control)
  management/commands/cleanup_merge_jobs.py   Deletes MergeJob rows + files older than --days
apps/pdf_split/         Split PDF feature, mounted at /api/v1/pdf/
  models.py             SplitJob
  services.py           SplitPDFService (all_pages/ranges/every_n/extract modes, ZIP packaging)
  management/commands/cleanup_split_jobs.py   Deletes SplitJob rows + files older than --days
apps/pdf_organize/      Organize PDF feature, mounted at /api/v1/pdf/
  models.py             OrganizeJob
  services.py           OrganizePDFService + validate_order() (1-based page-order validation)
  management/commands/cleanup_organize_jobs.py   Deletes OrganizeJob rows + files older than --days
apps/pdf_remove_pages/  Remove Pages feature, mounted at /api/v1/pdf/
  models.py             RemovePagesJob
  services.py           RemovePagesService + validate_pages_to_remove() (rejects removing every page)
  management/commands/cleanup_remove_pages_jobs.py   Deletes RemovePagesJob rows + files older than --days
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

### 5. Merge PDF

```
POST /api/v1/pdf/merge/
Content-Type: multipart/form-data
Authentication: none required (same as every other endpoint today)

Body:
  files: 2+ PDF files, sent as repeated "files" fields
  order: optional, 0-based permutation of the files' indices
         (e.g. [2, 0, 1]) - lets a client reorder without re-uploading;
         defaults to upload order when omitted
```

Validation: 2-20 files, each individually a real, non-corrupted, non-encrypted
PDF under 50 MB (extension + magic-byte + PyMuPDF-open checks - not just a
trusted `Content-Type` header), combined size under 150 MB, combined page
count under 3000.

Response `201`:
```json
{
  "success": true,
  "message": "PDFs merged successfully",
  "data": {
    "file_id": "a846ca76-6590-41e2-ae58-11f52b5a27b7",
    "owner_token": "6jmlbcinruPaAwdW0ByaBLu_9RBpLAGKGSTJDIGJxbM",
    "download_url": "http://localhost:8000/api/v1/pdf/merge/a846ca76-.../download/?token=6jmlbc...",
    "filename": "merged.pdf",
    "source_count": 2,
    "total_pages": 3
  }
}
```

`download_url` already has the token embedded - see [Ownership & access control](#ownership--access-control)
below for what enforces it and why.

Error response (validation failure, `400`, or processing failure, `500`):
```json
{
  "success": false,
  "message": "Invalid request.",
  "error_code": "VALIDATION_ERROR",
  "errors": { "files": ["At least 2 PDF files are required to merge."] }
}
```

Merge results (`MergeJob` rows + their output files) are not deleted
automatically - run `python manage.py cleanup_merge_jobs --days 7` on a
schedule (cron / Render cron job) to purge old ones, since there is no
background task runner in this project yet.

### 6. Split PDF

```
POST /api/v1/pdf/split/
Content-Type: multipart/form-data
Authentication: none required (same as every other endpoint today)

Body:
  file:   a single PDF
  mode:   "all_pages" | "ranges" | "every_n" | "extract"
  ranges: required for mode=ranges, e.g. "1-5,6-10,11"
          (commas and/or newlines as separators)
  n:      required for mode=every_n - split into consecutive chunks of
          n pages each (the last chunk may be shorter)
  pages:  required for mode=extract - repeated field, e.g.
          pages=3&pages=1&pages=4 - 1-based page numbers, combined into
          ONE output PDF in the exact order given (duplicates allowed)
```

**Overlapping/duplicate ranges rule** (mode=ranges): ranges are **not**
deduplicated, sorted, or merged. Each range produces exactly one output
file, in input order - `"1-3,2-5"` produces two files, and pages 2-3
simply appear in both. A reversed range (`"5-2"`) or any page outside
`1..total_pages` is rejected with a `400`.

If the operation produces exactly one output file (e.g. `extract`, or
`ranges`/`every_n` reducing to a single chunk), the response is a single
PDF. Otherwise the outputs are zipped.

Response `201` (multiple outputs):
```json
{
  "success": true,
  "message": "PDF split successfully",
  "data": {
    "file_id": "5ba6bee8-c574-4229-a3ff-2bbf16ed2b5e",
    "owner_token": "Zk3f...",
    "download_url": "http://localhost:8000/api/v1/pdf/split/5ba6bee8-.../download/?token=Zk3f...",
    "filename": "split_result.zip",
    "is_zip": true,
    "output_count": 3,
    "output_filenames": ["pages_1-3.pdf", "pages_4-5.pdf", "pages_6-7.pdf"],
    "source_pages": 7
  }
}
```

Error response (`400` validation, or `500` processing failure) follows the
same `{success, message, error_code, errors}` shape as Merge PDF.

Run `python manage.py cleanup_split_jobs --days 7` on a schedule to purge
old `SplitJob` rows and their output files.

### 7. Organize PDF

```
POST /api/v1/pdf/organize/
Content-Type: multipart/form-data
Authentication: none required (same as every other endpoint today)

Body:
  file:  a single PDF
  order: the full new page order, 1-based (page 1 is what a user sees as
         "page 1" - the API is 1-based, not 0-based, to match that),
         sent as repeated multipart fields - e.g. for a 5-page document
         reordered to [3,1,5,2,4]:
         order=3&order=1&order=5&order=2&order=4
         (the same repeated-field convention Merge's `order` and Split's
         `pages` already use)
```

**Validation is strict, not best-effort**: `order` must be a genuine
permutation of `1..page_count` - exactly `page_count` values, no
duplicates, no page skipped, nothing out of range, no non-integers
(including `true`/`false`, which Python's `bool` would otherwise pass as
`int`). Any violation is rejected with a specific message identifying
which pages were duplicated/missing/out-of-range - the backend never
silently drops, dedupes, or reorders around a bad request.

Response `201`:
```json
{
  "success": true,
  "message": "PDF organized successfully",
  "data": {
    "file_id": "b2f6e6b0-...",
    "owner_token": "Kx9f...",
    "download_url": "http://localhost:8000/api/v1/pdf/organize/b2f6e6b0-.../download/?token=Kx9f...",
    "filename": "organized.pdf",
    "page_count": 5
  }
}
```

Error response (`400` validation, or `500` processing failure) follows the
same `{success, message, error_code, errors}` shape as Merge/Split PDF.

Run `python manage.py cleanup_organize_jobs --days 7` on a schedule to purge
old `OrganizeJob` rows and their output files.

### 8. Remove Pages

```
POST /api/v1/pdf/remove-pages/
Content-Type: multipart/form-data
Authentication: none required (same as every other endpoint today)

Body:
  file:  a single PDF
  pages: 1-based page numbers to delete, sent as repeated multipart
         fields - e.g. to remove pages 2 and 4: pages=2&pages=4
         (the same repeated-field convention Merge/Split/Organize use)
```

**Validation**: every page number must be in range and not repeated, and
the request is rejected if it would remove every page - the resulting PDF
would be empty, which this feature does not support. Remaining pages keep
their original relative order; removal never reorders anything.

Response `201`:
```json
{
  "success": true,
  "message": "Pages removed successfully",
  "data": {
    "file_id": "9d1c...",
    "owner_token": "Rm2p...",
    "download_url": "http://localhost:8000/api/v1/pdf/remove-pages/9d1c-.../download/?token=Rm2p...",
    "filename": "pages_removed.pdf",
    "source_page_count": 5,
    "removed_pages": [2, 4],
    "output_page_count": 3
  }
}
```

Error response (`400` validation, or `500` processing failure) follows the
same `{success, message, error_code, errors}` shape as the other features.

Run `python manage.py cleanup_remove_pages_jobs --days 7` on a schedule to
purge old `RemovePagesJob` rows and their output files.

## Ownership & access control

**Architectural decision, not an oversight**: this product has no user
accounts, login, or session system today - no registration/login page
exists anywhere in the frontend, and the backend has no auth endpoints
beyond Django's own `/admin/`. Bolting on a full login flow just to gate
file access would be a much bigger, unasked-for product decision (a login
UI, password reset, email verification, ...), so this deliberately does
**not** do that.

**What's actually enforced instead** (`apps/common/ownership.py`,
`apps/common/views.py`, reused by every job-based feature): every job
(`MergeJob`, `SplitJob`, and anything built the same way going forward)
issues a random 32-byte bearer token at creation time, returned once in
that creation response (`data.owner_token`, and already embedded as
`?token=...` in `data.download_url`). Whoever holds that exact token can
view/download that one job's result; nobody else can - including another
anonymous visitor who happens to see or guess the job's UUID. The token
is checked with a constant-time comparison (`secrets.compare_digest`).

This closes a real gap: previously, a job's UUID alone - routinely visible
in URLs and API responses - was sufficient to download **any** job's
output, from any client, no secret required. A bearer token is a strict
improvement over that with zero login friction, and needs no changes when
real user accounts are eventually added: `job.user` (already a nullable FK
on every job model) is checked first and wins whenever the requester is
authenticated as that job's owner; the token remains as the fallback that
makes anonymous jobs private too.

**Where the file actually lives**: job output files are stored under
`private_media/` (a `FileSystemStorage` instance pointed outside
`MEDIA_ROOT` - see `apps/common/storage.py`), which no URL pattern in
`core/urls.py` serves. The *only* way to retrieve one is through the
per-feature `.../download/` endpoint, which checks ownership before
streaming the file - unlike `/media/...`, there's no raw path that bypasses
the token.

**Non-existence vs. wrong token**: both return an identical `404` with
`error_code: "NOT_FOUND"`. Confirming "this id exists, you just have the
wrong token" would let an attacker enumerate valid job ids even without
ever accessing their contents, so the two cases are indistinguishable by
design.

**Unauthenticated policy, explicitly**: every endpoint remains fully usable
without logging in (consistent with the rest of this product) - "access
control" here means *per-job* privacy via the token, not *login-gating*
the feature itself. A request with no token and no matching authenticated
user is simply treated as "not this job's owner."

**Known, deliberate scope boundary**: this layer is not retrofitted onto
`apps.docs_editor` (the original PDF text editor) in this pass - that
endpoint's access model (any UUID holder can view/edit/extract) is
unchanged, to avoid risk to an already-shipped, already-tested feature as
a side effect of unrelated work. If/when that's worth closing, it should
be its own deliberate change against `apps/docs_editor` specifically.

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
