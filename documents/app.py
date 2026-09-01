"""Document authoring and rendering service for the Letta agent stack.

Markdown is the single source of truth. Every other format is a disposable,
content-addressed render artifact produced by pandoc, with PDF passing through
the .docx so a downloaded pair is the same document rather than two lookalikes.

Standard library only, in the same shape as letta-openai-proxy.py.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import Request, urlopen


API_KEY = os.environ.get("DOCUMENTS_API_KEY", "")
DATA_DIR = Path(os.environ.get("DOCUMENTS_DATA_DIR", "/data"))
PORT = int(os.environ.get("DOCUMENTS_PORT", "8090"))
GOTENBERG_URL = os.environ.get("GOTENBERG_URL", "http://gotenberg:3000").rstrip("/")
REFERENCE_DOCX = Path(os.environ.get("DOCUMENTS_REFERENCE_DOCX", "/opt/reference.docx"))
INPUT_FORMAT = os.environ.get("DOCUMENTS_INPUT_FORMAT", "markdown")

# Delivery. openwebui uploads finished renders into Open WebUI's files API and
# links to them; capability publishes unguessable, expiring links from this
# service instead. The difference is confined to _download_url.
DELIVERY_MODE = os.environ.get("DOCUMENTS_DELIVERY_MODE", "openwebui").strip().lower()
PUBLIC_BASE_URL = os.environ.get("DOCUMENTS_PUBLIC_BASE_URL", "").rstrip("/")
OPENWEBUI_BASE_URL = os.environ.get("OPENWEBUI_BASE_URL", "http://open-webui:8080").rstrip("/")
OPENWEBUI_API_KEY = os.environ.get("OPENWEBUI_API_KEY", "")
LINK_TTL_SECONDS = int(os.environ.get("DOCUMENTS_LINK_TTL_SECONDS", "604800"))
RENDER_RETENTION_DAYS = int(os.environ.get("DOCUMENTS_RENDER_RETENTION_DAYS", "30"))

MAX_SOURCE_BYTES = int(os.environ.get("DOCUMENTS_MAX_SOURCE_BYTES", "1000000"))
MAX_UPLOAD_BYTES = int(os.environ.get("DOCUMENTS_MAX_UPLOAD_BYTES", "26214400"))
MAX_TITLE_CHARS = 200
PANDOC_TIMEOUT = int(os.environ.get("DOCUMENTS_PANDOC_TIMEOUT", "120"))
GOTENBERG_TIMEOUT = int(os.environ.get("DOCUMENTS_GOTENBERG_TIMEOUT", "150"))

DOCUMENT_ID = re.compile(r"^doc_[0-9a-f]{32}$")
LINK_TOKEN = re.compile(r"^[0-9a-f]{32}$")
LINKS_DIR = DATA_DIR / "_links"

# Formats this service promises to render well. Extending it is a one-line
# change; every entry is a format an agent may choose, so keep it deliberate.
FORMATS: dict[str, dict[str, str]] = {
    "md": {"extension": "md", "content_type": "text/markdown; charset=utf-8"},
    "docx": {
        "extension": "docx",
        "writer": "docx",
        "content_type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    },
    "pdf": {"extension": "pdf", "content_type": "application/pdf"},
    "html": {"extension": "html", "writer": "html", "content_type": "text/html; charset=utf-8"},
    "odt": {
        "extension": "odt",
        "writer": "odt",
        "content_type": "application/vnd.oasis.opendocument.text",
    },
    "txt": {"extension": "txt", "writer": "plain", "content_type": "text/plain; charset=utf-8"},
}

HTML_STYLE = """<style>
body { max-width: 46rem; margin: 3rem auto; padding: 0 1.25rem;
  font: 16px/1.6 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  color: #1a1a1a; background: #ffffff; }
h1, h2, h3, h4 { line-height: 1.25; margin: 2rem 0 .75rem; }
code, pre { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: .9em; }
pre { background: #f5f6f7; padding: .85rem 1rem; overflow-x: auto; border-radius: 4px; }
table { border-collapse: collapse; width: 100%; margin: 1.25rem 0; }
th, td { border: 1px solid #d5d9de; padding: .45rem .65rem; text-align: left; }
blockquote { margin: 1.25rem 0; padding-left: 1rem; border-left: 3px solid #d5d9de; color: #4a5158; }
img { max-width: 100%; }
</style>
"""


class ServiceError(Exception):
    """A client-visible failure with an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def log(message: str) -> None:
    print(f"documents: {message}", flush=True)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(title: str, fallback: str) -> str:
    """Filename-safe slug that keeps non-Latin scripts intact.

    ASCII-folding a Persian or CJK title leaves nothing behind, which used to
    fall the download filename back to the raw document id.
    """
    characters = []
    for character in unicodedata.normalize("NFC", title):
        if unicodedata.category(character)[0] in {"L", "N", "M"}:
            characters.append(character)
        else:
            characters.append("-")
    slug = re.sub(r"-+", "-", "".join(characters)).strip("-.").lower()
    return slug[:80] or fallback


def content_disposition(filename: str) -> str:
    """RFC 6266 disposition that survives a non-ASCII filename."""
    ascii_name = filename.encode("ascii", "replace").decode().replace('"', "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def document_dir(document_id: str) -> Path:
    """Resolve a document directory, rejecting anything that is not an id.

    The id reaches a path join, so this is the traversal boundary.
    """
    if not isinstance(document_id, str) or not DOCUMENT_ID.fullmatch(document_id):
        raise ServiceError(400, "document_id must match doc_<32 hex characters>")
    return DATA_DIR / document_id


def read_meta(directory: Path) -> dict[str, Any]:
    try:
        meta = json.loads((directory / "meta.json").read_text())
    except FileNotFoundError as error:
        raise ServiceError(404, "document not found") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ServiceError(500, "document metadata is unreadable") from error
    if not isinstance(meta, dict):
        raise ServiceError(500, "document metadata is unreadable")
    return meta


def write_meta(directory: Path, meta: dict[str, Any]) -> None:
    temporary = directory / "meta.json.tmp"
    temporary.write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    temporary.replace(directory / "meta.json")


def read_source(directory: Path) -> str:
    try:
        return (directory / "source.md").read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise ServiceError(404, "document not found") from error
    except (OSError, UnicodeDecodeError) as error:
        raise ServiceError(500, "document source is unreadable") from error


def require_string(payload: dict[str, Any], field: str, *, max_chars: int) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ServiceError(400, f"{field} must be a non-empty string")
    if len(value.encode()) > max_chars:
        raise ServiceError(400, f"{field} exceeds the {max_chars} byte limit")
    return value


def multipart_body(
    fields: dict[str, str], files: list[tuple[str, str, bytes, str]]
) -> tuple[bytes, str]:
    """Encode a multipart/form-data body without a third-party dependency."""
    boundary = f"----documents{secrets.token_hex(16)}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode()
        )
    for name, filename, content, content_type in files:
        parts.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; '
            f'filename="{filename}"\r\nContent-Type: {content_type}\r\n\r\n'.encode()
        )
        parts.append(content)
        parts.append(b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def run_pandoc(source: str, writer: str, output: Path) -> None:
    command = ["pandoc", "-f", INPUT_FORMAT, "-t", writer, "-o", str(output)]
    header: Path | None = None
    if writer == "docx" and REFERENCE_DOCX.is_file() and REFERENCE_DOCX.stat().st_size:
        command += ["--reference-doc", str(REFERENCE_DOCX)]
    if writer == "html":
        header = output.with_suffix(".header.html")
        header.write_text(HTML_STYLE, encoding="utf-8")
        command += ["--standalone", "--include-in-header", str(header)]
    try:
        completed = subprocess.run(
            command,
            input=source.encode("utf-8"),
            capture_output=True,
            timeout=PANDOC_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise ServiceError(504, "pandoc timed out while rendering the document") from error
    except OSError as error:
        raise ServiceError(500, f"pandoc is unavailable ({type(error).__name__})") from error
    finally:
        if header is not None:
            header.unlink(missing_ok=True)
    if completed.returncode != 0 or not output.is_file():
        detail = completed.stderr.decode(errors="replace").strip().splitlines()
        reason = detail[-1][:200] if detail else "no diagnostic output"
        raise ServiceError(502, f"pandoc failed to render the document: {reason}")


def gotenberg_pdf(filename: str, content: bytes) -> bytes:
    """Convert an office document to PDF through Gotenberg's LibreOffice route."""
    body, content_type = multipart_body(
        {},
        [("files", filename, content, mimetypes.guess_type(filename)[0] or "application/octet-stream")],
    )
    request = Request(
        f"{GOTENBERG_URL}/forms/libreoffice/convert",
        data=body,
        method="POST",
        headers={"Content-Type": content_type, "User-Agent": "letta-documents/1.0"},
    )
    try:
        with urlopen(request, timeout=GOTENBERG_TIMEOUT) as response:
            return response.read()
    except HTTPError as error:
        raise ServiceError(502, f"Gotenberg returned HTTP {error.code} while converting to PDF") from error
    except (URLError, OSError) as error:
        raise ServiceError(502, f"unable to reach Gotenberg ({type(error).__name__})") from error


def openwebui_upload(filename: str, content: bytes, content_type: str) -> str:
    """Deposit a finished render in Open WebUI and return its download URL.

    process=false skips text extraction and RAG embedding, which are slow,
    pointless, and knowledge-base polluting for a generated binary.
    """
    if not OPENWEBUI_API_KEY:
        raise ServiceError(
            503,
            "OPENWEBUI_API_KEY is unavailable; create an Open WebUI API key under "
            "Settings > Account or set DOCUMENTS_DELIVERY_MODE=capability",
        )
    body, boundary_type = multipart_body({}, [("file", filename, content, content_type)])
    request = Request(
        f"{OPENWEBUI_BASE_URL}/api/v1/files/?process=false",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {OPENWEBUI_API_KEY}",
            "Content-Type": boundary_type,
            "User-Agent": "letta-documents/1.0",
        },
    )
    try:
        with urlopen(request, timeout=GOTENBERG_TIMEOUT) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        raise ServiceError(502, f"Open WebUI returned HTTP {error.code} while storing the file") from error
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        raise ServiceError(502, f"unable to store the file in Open WebUI ({type(error).__name__})") from error
    file_id = payload.get("id") if isinstance(payload, dict) else None
    if not isinstance(file_id, str) or not file_id:
        raise ServiceError(502, "Open WebUI did not return a file id")
    base = PUBLIC_BASE_URL or OPENWEBUI_BASE_URL
    return f"{base}/api/v1/files/{quote(file_id, safe='')}/content/{quote(filename)}"


def capability_link(path: Path, filename: str, content_type: str) -> str:
    """Publish an unguessable, expiring link served by this service."""
    if not PUBLIC_BASE_URL:
        raise ServiceError(503, "DOCUMENTS_PUBLIC_BASE_URL is unavailable")
    LINKS_DIR.mkdir(parents=True, exist_ok=True)
    token = secrets.token_hex(16)
    (LINKS_DIR / f"{token}.json").write_text(
        json.dumps(
            {
                "path": str(path),
                "filename": filename,
                "content_type": content_type,
                "expires_at": time.time() + LINK_TTL_SECONDS,
            }
        )
    )
    return f"{PUBLIC_BASE_URL}/d/{token}/{quote(filename)}"


def delivery_ready() -> bool:
    """Whether the configured delivery mode can actually publish a link."""
    if DELIVERY_MODE == "capability":
        return bool(PUBLIC_BASE_URL)
    return DELIVERY_MODE == "openwebui" and bool(OPENWEBUI_API_KEY)


def deliver(path: Path, filename: str, content_type: str) -> str:
    if DELIVERY_MODE == "capability":
        return capability_link(path, filename, content_type)
    if DELIVERY_MODE != "openwebui":
        raise ServiceError(500, "DOCUMENTS_DELIVERY_MODE must be openwebui or capability")
    try:
        content = path.read_bytes()
    except OSError as error:
        raise ServiceError(500, "the finished render is unreadable") from error
    return openwebui_upload(filename, content, content_type.split(";")[0])


def sweep_renders() -> int:
    """Delete render artifacts untouched for the retention window."""
    if RENDER_RETENTION_DAYS <= 0:
        return 0
    cutoff = time.time() - RENDER_RETENTION_DAYS * 86400
    removed = 0
    for renders in DATA_DIR.glob("doc_*/renders"):
        for artifact in renders.iterdir():
            try:
                if artifact.is_file() and artifact.stat().st_mtime < cutoff:
                    artifact.unlink()
                    removed += 1
            except OSError:
                continue
    for link in LINKS_DIR.glob("*.json") if LINKS_DIR.is_dir() else []:
        try:
            record = json.loads(link.read_text())
            if float(record.get("expires_at", 0)) < time.time():
                link.unlink()
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return removed


def sweeper() -> None:
    while True:
        try:
            removed = sweep_renders()
            if removed:
                log(f"swept {removed} expired render artifact(s)")
        except OSError as error:
            log(f"sweep failed ({type(error).__name__})")
        time.sleep(3600)


def create_document(payload: dict[str, Any]) -> dict[str, Any]:
    title = require_string(payload, "title", max_chars=MAX_TITLE_CHARS)
    markdown = require_string(payload, "markdown", max_chars=MAX_SOURCE_BYTES)
    document_id = f"doc_{uuid.uuid4().hex}"
    directory = DATA_DIR / document_id
    (directory / "renders").mkdir(parents=True, exist_ok=False)
    (directory / "source.md").write_text(markdown, encoding="utf-8")
    timestamp = now()
    meta = {
        "document_id": document_id,
        "title": title.strip(),
        "created_at": timestamp,
        "updated_at": timestamp,
        "owner": str(payload.get("owner", ""))[:200],
        "revision": 1,
    }
    write_meta(directory, meta)
    return {**meta, "chars": len(markdown)}


def get_document(document_id: str, query: dict[str, list[str]]) -> dict[str, Any]:
    directory = document_dir(document_id)
    meta = read_meta(directory)
    source = read_source(directory)
    try:
        max_chars = int(query.get("max_chars", ["50000"])[0])
    except (TypeError, ValueError) as error:
        raise ServiceError(400, "max_chars must be an integer") from error
    max_chars = max(1000, min(max_chars, MAX_SOURCE_BYTES))
    truncated = len(source) > max_chars
    return {
        **meta,
        "chars": len(source),
        "truncated": truncated,
        "markdown": source[:max_chars] + ("\n[TRUNCATED]" if truncated else ""),
    }


def update_document(document_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    directory = document_dir(document_id)
    meta = read_meta(directory)
    source = read_source(directory)

    replacement = payload.get("str_replace")
    if "markdown" in payload:
        source = require_string(payload, "markdown", max_chars=MAX_SOURCE_BYTES)
    elif replacement is not None:
        if not isinstance(replacement, dict):
            raise ServiceError(400, "str_replace must be an object with old and new")
        old = replacement.get("old")
        new = replacement.get("new")
        if not isinstance(old, str) or not old:
            raise ServiceError(400, "str_replace.old must be a non-empty string")
        if not isinstance(new, str):
            raise ServiceError(400, "str_replace.new must be a string")
        occurrences = source.count(old)
        if occurrences == 0:
            raise ServiceError(400, "str_replace.old does not appear in the document")
        if occurrences > 1:
            raise ServiceError(400, f"str_replace.old appears {occurrences} times; make it unique")
        source = source.replace(old, new, 1)
        if len(source.encode()) > MAX_SOURCE_BYTES:
            raise ServiceError(400, f"the updated document exceeds the {MAX_SOURCE_BYTES} byte limit")
    elif "title" not in payload:
        raise ServiceError(400, "supply markdown, str_replace, or title")

    if "title" in payload:
        meta["title"] = require_string(payload, "title", max_chars=MAX_TITLE_CHARS).strip()

    (directory / "source.md").write_text(source, encoding="utf-8")
    meta["revision"] = int(meta.get("revision", 1)) + 1
    meta["updated_at"] = now()
    write_meta(directory, meta)
    return {**meta, "chars": len(source)}


def render_document(document_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    directory = document_dir(document_id)
    meta = read_meta(directory)
    source = read_source(directory)
    output_format = payload.get("format", "docx")
    if not isinstance(output_format, str) or output_format not in FORMATS:
        raise ServiceError(400, f"format must be one of {sorted(FORMATS)}")

    spec = FORMATS[output_format]
    extension = spec["extension"]
    digest = hashlib.sha256(f"{output_format}\0".encode() + source.encode("utf-8")).hexdigest()
    renders = directory / "renders"
    renders.mkdir(parents=True, exist_ok=True)
    artifact = renders / f"{digest}.{extension}"
    sidecar = renders / f"{digest}.{extension}.json"
    filename = f"{slugify(meta.get('title', ''), document_id)}.{extension}"

    if not artifact.is_file():
        if output_format == "md":
            artifact.write_text(source, encoding="utf-8")
        elif output_format == "pdf":
            # The PDF passes through the .docx so the two files a user
            # downloads are the same document, not two lookalikes.
            intermediate = renders / f"{digest}.intermediate.docx"
            run_pandoc(source, "docx", intermediate)
            artifact.write_bytes(gotenberg_pdf(f"{Path(filename).stem}.docx", intermediate.read_bytes()))
            intermediate.unlink(missing_ok=True)
        else:
            run_pandoc(source, spec["writer"], artifact)
    else:
        os.utime(artifact, None)

    cached = False
    if sidecar.is_file():
        try:
            record = json.loads(sidecar.read_text())
            if isinstance(record.get("download_url"), str) and record.get("filename") == filename:
                cached = True
                download_url = record["download_url"]
        except (OSError, ValueError, json.JSONDecodeError):
            cached = False
    if not cached:
        download_url = deliver(artifact, filename, spec["content_type"])
        sidecar.write_text(json.dumps({"filename": filename, "download_url": download_url}))

    return {
        "document_id": document_id,
        "title": meta.get("title", ""),
        "revision": meta.get("revision", 1),
        "format": output_format,
        "filename": filename,
        "bytes": artifact.stat().st_size,
        "cached": cached,
        "download_url": download_url,
    }


def list_documents(query: dict[str, list[str]]) -> dict[str, Any]:
    try:
        limit = max(1, min(int(query.get("limit", ["25"])[0]), 100))
        offset = max(0, int(query.get("offset", ["0"])[0]))
    except (TypeError, ValueError) as error:
        raise ServiceError(400, "limit and offset must be integers") from error
    records: list[dict[str, Any]] = []
    for directory in DATA_DIR.glob("doc_*"):
        if not DOCUMENT_ID.fullmatch(directory.name) or not directory.is_dir():
            continue
        try:
            meta = read_meta(directory)
        except ServiceError:
            continue
        records.append(
            {
                "document_id": meta.get("document_id", directory.name),
                "title": meta.get("title", ""),
                "revision": meta.get("revision", 1),
                "updated_at": meta.get("updated_at", ""),
            }
        )
    records.sort(key=lambda record: record.get("updated_at", ""), reverse=True)
    return {"total": len(records), "limit": limit, "offset": offset, "documents": records[offset : offset + limit]}


def delete_document(document_id: str) -> dict[str, Any]:
    directory = document_dir(document_id)
    if not directory.is_dir():
        raise ServiceError(404, "document not found")
    shutil.rmtree(directory, ignore_errors=True)
    return {"document_id": document_id, "deleted": True}


def convert_file(payload: dict[str, Any]) -> dict[str, Any]:
    filename = require_string(payload, "filename", max_chars=255)
    source_name = Path(filename.replace("\\", "/")).name
    if not source_name or source_name.startswith("."):
        raise ServiceError(400, "filename must include a name and extension")
    encoded = payload.get("content_base64")
    if not isinstance(encoded, str) or not encoded:
        raise ServiceError(400, "content_base64 must be a non-empty base64 string")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as error:
        raise ServiceError(400, "content_base64 is not valid base64") from error
    if not content:
        raise ServiceError(400, "content_base64 decoded to an empty file")
    if len(content) > MAX_UPLOAD_BYTES:
        raise ServiceError(400, f"the file exceeds the {MAX_UPLOAD_BYTES} byte limit")

    pdf = gotenberg_pdf(source_name, content)
    conversions = DATA_DIR / "_conversions"
    conversions.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(content).hexdigest()
    artifact = conversions / f"{digest}.pdf"
    artifact.write_bytes(pdf)
    output_name = f"{slugify(Path(source_name).stem, digest[:12])}.pdf"
    return {
        "filename": output_name,
        "source_filename": source_name,
        "format": "pdf",
        "bytes": len(pdf),
        "download_url": deliver(artifact, output_name, "application/pdf"),
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "letta-documents/1.0"

    def log_message(self, message: str, *args: object) -> None:
        print(f"{self.client_address[0]} - {message % args}", flush=True)

    def _send_bytes(
        self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra or {}).items():
            self.send_header(name, value)
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)
        self.close_connection = True

    def _send_json(self, status: int, value: Any) -> None:
        self._send_bytes(status, json.dumps(value, ensure_ascii=False).encode(), "application/json")

    def _body(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ServiceError(400, "Content-Length must be an integer") from error
        if length > MAX_UPLOAD_BYTES + MAX_SOURCE_BYTES:
            raise ServiceError(413, "the request body is too large")
        raw = self.rfile.read(length) if length else b""
        if not raw:
            return {}
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError) as error:
            raise ServiceError(400, "the request body must be JSON") from error
        if not isinstance(payload, dict):
            raise ServiceError(400, "the request body must be a JSON object")
        return payload

    def _authorize(self) -> None:
        if not API_KEY:
            raise ServiceError(500, "DOCUMENTS_API_KEY is unavailable")
        header = self.headers.get("Authorization", "")
        scheme, _, presented = header.partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(presented.strip(), API_KEY):
            raise ServiceError(401, "a valid bearer credential is required")

    def _capability_download(self, token: str, filename: str) -> None:
        if DELIVERY_MODE != "capability" or not LINK_TOKEN.fullmatch(token):
            raise ServiceError(404, "not found")
        try:
            record = json.loads((LINKS_DIR / f"{token}.json").read_text())
        except (OSError, ValueError, json.JSONDecodeError) as error:
            raise ServiceError(404, "not found") from error
        if float(record.get("expires_at", 0)) < time.time():
            raise ServiceError(410, "this download link has expired")
        if record.get("filename") != filename:
            raise ServiceError(404, "not found")
        path = Path(record.get("path", ""))
        try:
            path.relative_to(DATA_DIR)
            content = path.read_bytes()
        except (ValueError, OSError) as error:
            raise ServiceError(404, "not found") from error
        self._send_bytes(
            200,
            content,
            record.get("content_type", "application/octet-stream"),
            {"Content-Disposition": content_disposition(filename)},
        )

    def _dispatch(self) -> None:
        parsed = urlsplit(self.path)
        segments = [segment for segment in parsed.path.split("/") if segment]
        query = parse_qs(parsed.query)
        method = self.command

        if method in {"GET", "HEAD"} and segments == ["health"]:
            self._send_json(
                200,
                {"status": True, "delivery_mode": DELIVERY_MODE, "delivery_ready": delivery_ready()},
            )
            return
        if method == "GET" and len(segments) == 3 and segments[0] == "d":
            self._capability_download(segments[1], segments[2])
            return

        self._authorize()

        if method == "POST" and segments == ["documents"]:
            self._send_json(201, create_document(self._body()))
        elif method == "GET" and segments == ["documents"]:
            self._send_json(200, list_documents(query))
        elif method == "GET" and len(segments) == 2 and segments[0] == "documents":
            self._send_json(200, get_document(segments[1], query))
        elif method == "PATCH" and len(segments) == 2 and segments[0] == "documents":
            self._send_json(200, update_document(segments[1], self._body()))
        elif method == "DELETE" and len(segments) == 2 and segments[0] == "documents":
            self._send_json(200, delete_document(segments[1]))
        elif method == "POST" and len(segments) == 3 and segments[0] == "documents" and segments[2] == "render":
            self._send_json(200, render_document(segments[1], self._body()))
        elif method == "POST" and segments == ["convert"]:
            self._send_json(200, convert_file(self._body()))
        else:
            raise ServiceError(404, "not found")

    def _handle(self) -> None:
        try:
            self._dispatch()
        except ServiceError as error:
            self._send_json(error.status, {"error": error.message})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as error:  # noqa: BLE001 - never leak a traceback to a caller
            log(f"unhandled {type(error).__name__} for {self.command} {self.path.split('?')[0]}")
            self._send_json(500, {"error": "the documents service failed to handle the request"})

    do_GET = _handle  # noqa: N815
    do_HEAD = _handle  # noqa: N815
    do_POST = _handle  # noqa: N815
    do_PATCH = _handle  # noqa: N815
    do_DELETE = _handle  # noqa: N815


def main() -> int:
    if not API_KEY:
        log("error: DOCUMENTS_API_KEY is unset")
        return 1
    if DELIVERY_MODE not in {"openwebui", "capability"}:
        log("error: DOCUMENTS_DELIVERY_MODE must be openwebui or capability")
        return 1
    try:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        log(f"error: cannot use {DATA_DIR} ({type(error).__name__})")
        return 1
    if not delivery_ready():
        # Not fatal: renders still work, and the credential can be supplied on
        # the next recreate. Failing here would leave the stack unhealthy out
        # of the box, before the operator can create an Open WebUI key.
        log(
            "warning: delivery mode "
            f"{DELIVERY_MODE} is not configured; document_render will fail until "
            "OPENWEBUI_API_KEY (openwebui) or DOCUMENTS_PUBLIC_BASE_URL (capability) is set"
        )
    sweep_renders()
    threading.Thread(target=sweeper, daemon=True).start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    log(f"listening on 0.0.0.0:{PORT}; delivery mode {DELIVERY_MODE}")
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
