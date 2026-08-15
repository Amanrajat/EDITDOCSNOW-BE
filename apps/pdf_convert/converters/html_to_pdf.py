"""
HTML/Webpage -> PDF, via WeasyPrint. Security is the point of this
module, not an afterthought: every network fetch this converter performs
- the top-level URL, and every resource (image/stylesheet/font) the
rendered page references - goes through the same SSRF validation before
a single byte is requested.

Blocked outright:
- non-http(s) schemes (file://, ftp://, custom schemes - no local
  filesystem access, no protocol smuggling)
- any hostname resolving to a private/loopback/link-local/reserved/
  multicast IP (RFC1918 ranges, 127.0.0.0/8, 169.254.0.0/16 - which
  covers cloud metadata endpoints like 169.254.169.254 - ::1, fc00::/7,
  fe80::/10, etc. via Python's ipaddress.is_private/is_loopback/etc.)
- non-default ports (only 80/443 - blocks reaching internal services like
  Redis/Postgres/admin panels on arbitrary internal ports)

Each redirect hop is re-validated before being followed (closes the
"safe first URL, malicious redirect target" bypass) - up to a small
redirect limit. Requests use a timeout and a response-size cap.

Known limitation: there is a narrow DNS-rebinding TOCTOU window (a
hostname could theoretically resolve safely at validation time and
differently once the actual TCP connection is opened moments later) -
closing that completely requires pinning the validated IP for the
connection itself (e.g. a custom transport adapter), which this module
does not implement. This is documented, not silently ignored.
"""

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import fitz
import requests
import weasyprint

DEFAULT_TIMEOUT_SECONDS = 15
MAX_REDIRECTS = 5
MAX_CONTENT_BYTES = 20 * 1024 * 1024  # 20 MB
ALLOWED_SCHEMES = {"http", "https"}
BLOCKED_HOSTNAMES = {"localhost", "metadata.google.internal"}
ALLOWED_PORTS = {80, 443}


class HtmlToPdfError(Exception):
    """A user-facing, 400/500-worthy error: bad input, a URL blocked by
    SSRF protection, a fetch failure, or a render failure."""


def _is_blocked_ip(ip_str):
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True  # can't parse it -> treat as unsafe, not as "allowed"
    return (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def _resolve_hostname_ips(hostname):
    try:
        infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise HtmlToPdfError(f"Could not resolve host '{hostname}'.")
    ips = {info[4][0] for info in infos}
    if not ips:
        raise HtmlToPdfError(f"Could not resolve host '{hostname}'.")
    return ips


def validate_url(url):
    """Raises HtmlToPdfError if `url` is unsafe to fetch. See module
    docstring for exactly what's blocked and why."""
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise HtmlToPdfError(f"Only http/https URLs are supported (got scheme {parsed.scheme!r}).")
    if not parsed.hostname:
        raise HtmlToPdfError("URL must include a hostname.")
    if parsed.hostname.lower() in BLOCKED_HOSTNAMES:
        raise HtmlToPdfError("This host is blocked.")
    if parsed.port is not None and parsed.port not in ALLOWED_PORTS:
        raise HtmlToPdfError("Only default HTTP (80) / HTTPS (443) ports are allowed.")

    for ip in _resolve_hostname_ips(parsed.hostname):
        if _is_blocked_ip(ip):
            raise HtmlToPdfError(
                "This URL resolves to a private/internal/reserved address and cannot be fetched."
            )


def _safe_fetch(url, timeout=DEFAULT_TIMEOUT_SECONDS):
    """Fetches `url`, re-validating (and thus re-resolving/re-blocking)
    at every redirect hop rather than trusting requests' own
    allow_redirects, which would only validate the first URL."""
    current_url = url
    for _ in range(MAX_REDIRECTS):
        validate_url(current_url)
        try:
            response = requests.get(
                current_url, timeout=timeout, allow_redirects=False, stream=True,
                headers={"User-Agent": "EditDocsNow-HtmlToPdf/1.0"},
            )
        except requests.RequestException as exc:
            raise HtmlToPdfError(f"Could not fetch '{current_url}': {exc}")

        if response.is_redirect or response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise HtmlToPdfError("Redirect response missing a Location header.")
            current_url = urljoin(current_url, location)
            continue

        content = response.raw.read(MAX_CONTENT_BYTES + 1, decode_content=True)
        response.close()
        if len(content) > MAX_CONTENT_BYTES:
            raise HtmlToPdfError("The response exceeded the maximum allowed size.")
        return content, response.headers.get("Content-Type", "")

    raise HtmlToPdfError("Too many redirects.")


def _safe_url_fetcher(url):
    """Passed to WeasyPrint so every resource it loads while rendering
    (images, stylesheets, fonts) - not just the top-level page - goes
    through the same SSRF validation, not just the initial request."""
    parsed = urlparse(url)
    if parsed.scheme == "data":
        return weasyprint.default_url_fetcher(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise HtmlToPdfError(f"Blocked resource scheme: {parsed.scheme!r}.")

    content, content_type = _safe_fetch(url)
    return {"string": content, "mime_type": (content_type.split(";")[0].strip() or None)}


def validate_options(page_size, orientation):
    if page_size not in ("A4", "Letter"):
        raise HtmlToPdfError(f"page_size must be 'A4' or 'Letter' (got {page_size!r}).")
    if orientation not in ("portrait", "landscape"):
        raise HtmlToPdfError(f"orientation must be 'portrait' or 'landscape' (got {orientation!r}).")


def convert(url=None, html=None, page_size="A4", orientation="portrait"):
    """Exactly one of `url`/`html` must be given. Returns (pdf_bytes, metadata dict)."""
    validate_options(page_size, orientation)
    if bool(url) == bool(html):
        raise HtmlToPdfError("Provide exactly one of `url` or `html`.")

    stylesheet = weasyprint.CSS(string=f"@page {{ size: {page_size.lower()} {orientation}; margin: 1cm; }}")

    if url:
        validate_url(url)
        content, _content_type = _safe_fetch(url)
        document = weasyprint.HTML(string=content, base_url=url, url_fetcher=_safe_url_fetcher)
    else:
        if len(html.encode("utf-8", errors="ignore")) > MAX_CONTENT_BYTES:
            raise HtmlToPdfError("HTML input exceeds the maximum allowed size.")
        # No base_url: relative resource references in raw pasted HTML
        # have nothing safe to resolve against, so they simply fail to
        # load rather than reaching somewhere unintended.
        document = weasyprint.HTML(string=html, url_fetcher=_safe_url_fetcher)

    try:
        pdf_bytes = document.write_pdf(stylesheets=[stylesheet])
    except HtmlToPdfError:
        raise
    except Exception as exc:
        raise HtmlToPdfError(f"Rendering failed: {exc}")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    try:
        page_count = len(doc)
    finally:
        doc.close()

    metadata = {"page_count": page_count, "source": "url" if url else "html"}
    return pdf_bytes, metadata
