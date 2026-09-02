"""Pinned, read-only repository workspaces and review progress records.

The service deliberately uses only Python's standard library and git. It never
executes repository content, installs dependencies, enables hooks, initializes
submodules, or invokes Git LFS. Workspaces are short lived and owned by a review
record so cleanup is a service invariant rather than prompt-only control flow.
"""

from __future__ import annotations

import base64
import fnmatch
import hmac
import json
import os
import re
import selectors
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlsplit


API_KEY = os.environ.get("REVIEW_API_KEY", "")
GITLAB_BASE_URL = os.environ.get("GITLAB_BASE_URL", "").rstrip("/")
GITLAB_WORKSPACE_TOKEN = os.environ.get("GITLAB_WORKSPACE_TOKEN", "")
DATA_DIR = Path(os.environ.get("REVIEW_DATA_DIR", "/data"))
PORT = int(os.environ.get("REVIEW_PORT", "8091"))
WORKSPACE_TTL_SECONDS = int(os.environ.get("WORKSPACE_TTL_SECONDS", "2700"))
WORKSPACE_MAX_CONCURRENT = int(os.environ.get("WORKSPACE_MAX_CONCURRENT", "3"))
WORKSPACE_MAX_BYTES = int(os.environ.get("WORKSPACE_MAX_BYTES", "2147483648"))
WORKSPACE_FETCH_DEPTH = int(os.environ.get("WORKSPACE_FETCH_DEPTH", "50"))
REVIEW_RECORD_RETENTION_DAYS = int(os.environ.get("REVIEW_RECORD_RETENTION_DAYS", "30"))
SUBPROCESS_TIMEOUT = 10
MAX_REQUEST_BYTES = 1_000_000
MAX_GIT_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_WORKSPACE_CALLS = 25
MAX_SEARCH_CALLS = 4
MAX_FILE_LINES = 2000
MAX_SEARCH_MATCHES = 200
MAX_EVENT_CHARS = 240

REVIEWS_DIR = DATA_DIR / "reviews"
WORKSPACES_DIR = DATA_DIR / "workspaces"
TRASH_DIR = DATA_DIR / "_trash"
REVIEW_ID = re.compile(r"^rev_[0-9a-f]{32}$")
WORKSPACE_ID = re.compile(r"^ws_[0-9a-f]{32}$")
SHA = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
PROJECT = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$")
HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
EVENT_STAGE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
SENSITIVE_EVENT = re.compile(
    r"(?i)(authorization|private-token|api[_ -]?key|password|secret|bearer\s|glpat-|token\s*[=:])"
)
SECRET_PATTERNS = (".env*", "*.pem", "*.key", "id_rsa*", "*.p12", "*.keystore")
LOCK = threading.RLock()
STOP_EVENT = threading.Event()

GIT_CONFIG = (
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.symlinks=false",
    "-c", "core.attributesFile=/dev/null",
    "-c", "protocol.allow=never",
    "-c", "protocol.https.allow=always",
)


class ServiceError(Exception):
    """A client-visible failure with an HTTP status."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path, missing: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ServiceError(404, missing) from error
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ServiceError(500, "stored metadata is unreadable") from error
    if not isinstance(value, dict):
        raise ServiceError(500, "stored metadata is unreadable")
    return value


def review_dir(review_id: str) -> Path:
    if not isinstance(review_id, str) or not REVIEW_ID.fullmatch(review_id):
        raise ServiceError(400, "review_id is invalid")
    return REVIEWS_DIR / review_id


def workspace_dir(workspace_id: str) -> Path:
    if not isinstance(workspace_id, str) or not WORKSPACE_ID.fullmatch(workspace_id):
        raise ServiceError(400, "workspace_id is invalid")
    return WORKSPACES_DIR / workspace_id


def load_review(review_id: str) -> dict[str, Any]:
    return read_json(review_dir(review_id) / "record.json", "review not found")


def save_review(record: dict[str, Any]) -> None:
    record["updated_at"] = now()
    write_json(review_dir(record["review_id"]) / "record.json", record)


def load_workspace(workspace_id: str) -> tuple[Path, dict[str, Any]]:
    directory = workspace_dir(workspace_id)
    meta = read_json(directory / "meta.json", "workspace not found")
    if parse_time(meta["expires_at"]) <= datetime.now(timezone.utc):
        discard_workspace(workspace_id, "expired")
        raise ServiceError(410, "workspace expired")
    return directory, meta


def safe_repository_path(repo: Path, candidate: str) -> Path:
    if not isinstance(candidate, str) or not candidate or len(candidate.encode()) > 4096:
        raise ServiceError(400, "path must be a non-empty repository-relative path")
    pure = Path(candidate)
    if pure.is_absolute() or any(part in {"", ".", ".."} for part in pure.parts):
        raise ServiceError(400, "path must be a confined repository-relative path")
    try:
        resolved = (repo / pure).resolve(strict=False)
        resolved.relative_to(repo.resolve())
    except (OSError, ValueError) as error:
        raise ServiceError(400, "path escapes the workspace") from error
    return resolved


def secret_path(candidate: str) -> bool:
    return any(fnmatch.fnmatch(part.lower(), pattern) for part in Path(candidate).parts for pattern in SECRET_PATTERNS)


def git_environment() -> dict[str, str]:
    path = os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")
    return {
        "PATH": path,
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_SYSTEM": "/dev/null",
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_LFS_SKIP_SMUDGE": "1",
    }


def git_authorization_header() -> str:
    credentials = base64.b64encode(f"oauth2:{GITLAB_WORKSPACE_TOKEN}".encode()).decode()
    return f"Authorization: Basic {credentials}"


def run_git(
    arguments: list[str], cwd: Path, *, timeout: int = SUBPROCESS_TIMEOUT,
    max_output: int = MAX_GIT_OUTPUT_BYTES, token: bool = False,
    allowed_returncodes: tuple[int, ...] = (0,),
) -> bytes:
    command = ["git", *GIT_CONFIG]
    if token:
        command.extend(("-c", f"http.extraHeader={git_authorization_header()}"))
    command.extend(arguments)
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=git_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as error:
        raise ServiceError(500, "git is unavailable") from error
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                raise ServiceError(504, "git operation timed out")
            ready = selector.select(min(remaining, 0.25))
            if ready:
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    break
                output.extend(chunk)
                if len(output) > max_output:
                    process.kill()
                    raise ServiceError(413, "git output exceeded the service limit")
            elif process.poll() is not None:
                chunk = process.stdout.read()
                output.extend(chunk)
                break
        return_code = process.wait(timeout=max(deadline - time.monotonic(), 0.01))
    except subprocess.TimeoutExpired as error:
        process.kill()
        raise ServiceError(504, "git operation timed out") from error
    finally:
        selector.close()
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdout.close()
    if return_code not in allowed_returncodes:
        raise ServiceError(502, "git operation failed")
    return bytes(output)


def directory_size(directory: Path) -> int:
    total = 0
    for root, directories, files in os.walk(directory, followlinks=False):
        directories[:] = [name for name in directories if not (Path(root) / name).is_symlink()]
        for name in files:
            try:
                total += (Path(root) / name).lstat().st_size
            except FileNotFoundError:
                continue
    return total


def total_workspace_bytes(exclude: Path | None = None) -> int:
    total = 0
    for directory in WORKSPACES_DIR.glob("ws_*"):
        if exclude is not None and directory == exclude:
            continue
        try:
            meta = read_json(directory / "meta.json", "workspace not found")
            total += int(meta.get("size_bytes", 0))
        except (ServiceError, TypeError, ValueError):
            total += directory_size(directory)
    return total


def append_event(review_id: str, stage: str, detail: str) -> dict[str, Any]:
    if not EVENT_STAGE.fullmatch(stage):
        raise ServiceError(400, "stage must be a short lowercase identifier")
    if not isinstance(detail, str) or not detail.strip():
        raise ServiceError(400, "detail must be a non-empty string")
    detail = detail.strip()
    if len(detail) > MAX_EVENT_CHARS or "\n" in detail or "\r" in detail:
        raise ServiceError(400, f"detail must be one line of at most {MAX_EVENT_CHARS} characters")
    if SENSITIVE_EVENT.search(detail) or any(character in detail for character in "{}[]"):
        raise ServiceError(400, "detail resembles a credential or raw payload")
    with LOCK:
        directory = review_dir(review_id)
        record = load_review(review_id)
        cursor = int(record.get("event_cursor", 0)) + 1
        event = {"cursor": cursor, "at": now(), "stage": stage, "detail": detail}
        with (directory / "events.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")
        record["event_cursor"] = cursor
        save_review(record)
        return event


def reserve_call(meta: dict[str, Any], *, search: bool = False) -> None:
    with LOCK:
        record = load_review(meta["review_id"])
        calls = int(record.get("workspace_calls", 0))
        searches = int(record.get("search_calls", 0))
        if calls >= MAX_WORKSPACE_CALLS:
            raise ServiceError(429, f"workspace call budget exhausted ({MAX_WORKSPACE_CALLS})")
        if search and searches >= MAX_SEARCH_CALLS:
            raise ServiceError(429, f"workspace search budget exhausted ({MAX_SEARCH_CALLS})")
        record["workspace_calls"] = calls + 1
        if search:
            record["search_calls"] = searches + 1
        record["coverage"] = {
            "workspace_calls_used": calls + 1,
            "workspace_calls_limit": MAX_WORKSPACE_CALLS,
            "searches_used": searches + (1 if search else 0),
            "searches_limit": MAX_SEARCH_CALLS,
        }
        save_review(record)


def discard_workspace(workspace_id: str, reason: str = "discarded") -> bool:
    directory = workspace_dir(workspace_id)
    with LOCK:
        if not directory.exists():
            return False
        try:
            meta = read_json(directory / "meta.json", "workspace not found")
        except ServiceError:
            meta = {}
        trashed = TRASH_DIR / f"{workspace_id}-{uuid.uuid4().hex}"
        directory.replace(trashed)
        review_id = meta.get("review_id")
        if isinstance(review_id, str):
            try:
                record = load_review(review_id)
                record["workspace_id"] = None
                record["workspace_status"] = reason
                record["workspace_discarded_at"] = now()
                save_review(record)
            except ServiceError:
                pass
    shutil.rmtree(trashed, ignore_errors=True)
    return True


def close_review(review_id: str, reason: str = "completed") -> dict[str, Any]:
    with LOCK:
        record = load_review(review_id)
        workspace_id = record.get("workspace_id")
        record["status"] = "closed"
        record["closed_reason"] = reason[:80]
        record["closed_at"] = now()
        save_review(record)
    discarded = bool(isinstance(workspace_id, str) and discard_workspace(workspace_id, reason))
    with LOCK:
        record = load_review(review_id)
    return {"review_id": review_id, "status": record["status"], "workspace_discarded": discarded}


def cleanup_once() -> None:
    current = datetime.now(timezone.utc)
    for directory in list(WORKSPACES_DIR.glob("ws_*")):
        try:
            meta = read_json(directory / "meta.json", "workspace not found")
            if parse_time(meta["expires_at"]) <= current:
                discard_workspace(directory.name, "expired")
        except (ServiceError, KeyError, TypeError, ValueError):
            try:
                modified = datetime.fromtimestamp(directory.stat().st_mtime, timezone.utc)
                if modified + timedelta(seconds=WORKSPACE_TTL_SECONDS) <= current:
                    discard_workspace(directory.name, "expired")
            except FileNotFoundError:
                pass
    for directory in list(TRASH_DIR.iterdir()):
        if directory.is_dir():
            shutil.rmtree(directory, ignore_errors=True)
    cutoff = current - timedelta(days=REVIEW_RECORD_RETENTION_DAYS)
    for directory in list(REVIEWS_DIR.glob("rev_*")):
        try:
            record = read_json(directory / "record.json", "review not found")
            updated = parse_time(record["updated_at"])
            if updated <= cutoff:
                workspace_id = record.get("workspace_id")
                if isinstance(workspace_id, str):
                    discard_workspace(workspace_id, "record_expired")
                shutil.rmtree(directory, ignore_errors=True)
        except (ServiceError, KeyError, TypeError, ValueError):
            continue


def cleanup_loop() -> None:
    interval = max(1, min(60, WORKSPACE_TTL_SECONDS // 4 or 1))
    while not STOP_EVENT.wait(interval):
        try:
            cleanup_once()
        except Exception as error:  # keep the backstop alive without leaking request data
            print(f"review: cleanup failed ({type(error).__name__})", flush=True)


def create_review(payload: dict[str, Any]) -> dict[str, Any]:
    project = payload.get("project")
    iid = payload.get("merge_request_iid")
    head_sha = payload.get("expected_head_sha")
    if not isinstance(project, str) or not PROJECT.fullmatch(project):
        raise ServiceError(400, "project must be a full GitLab project path")
    if not isinstance(iid, int) or isinstance(iid, bool) or iid < 1:
        raise ServiceError(400, "merge_request_iid must be a positive integer")
    if not isinstance(head_sha, str) or not SHA.fullmatch(head_sha):
        raise ServiceError(400, "expected_head_sha must be a full commit SHA")
    review_id = f"rev_{uuid.uuid4().hex}"
    directory = review_dir(review_id)
    directory.mkdir()
    timestamp = now()
    record = {
        "review_id": review_id,
        "project": project,
        "merge_request_iid": iid,
        "expected_head_sha": head_sha,
        "status": "active",
        "workspace_id": None,
        "workspace_status": "not_opened",
        "workspace_calls": 0,
        "search_calls": 0,
        "coverage": {
            "workspace_calls_used": 0,
            "workspace_calls_limit": MAX_WORKSPACE_CALLS,
            "searches_used": 0,
            "searches_limit": MAX_SEARCH_CALLS,
        },
        "event_cursor": 0,
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    write_json(directory / "record.json", record)
    (directory / "events.jsonl").touch()
    return record


def create_workspace(payload: dict[str, Any]) -> dict[str, Any]:
    review_id = payload.get("review_id")
    project = payload.get("project")
    iid = payload.get("merge_request_iid")
    target_branch = payload.get("target_branch")
    expected_head_sha = payload.get("expected_head_sha")
    base_sha = payload.get("base_sha")
    record = load_review(review_id)
    if record.get("status") != "active":
        raise ServiceError(409, "review is already closed")
    if record.get("workspace_id"):
        raise ServiceError(409, "review already owns a workspace")
    if project != record.get("project") or iid != record.get("merge_request_iid"):
        raise ServiceError(400, "workspace coordinates do not match the review")
    if expected_head_sha != record.get("expected_head_sha"):
        raise ServiceError(400, "workspace head does not match the review")
    if not isinstance(target_branch, str) or not target_branch or len(target_branch) > 255 or any(
        value in target_branch for value in ("\x00", "\n", "\r", "..", "@{", "\\")
    ):
        raise ServiceError(400, "target_branch is invalid")
    if not isinstance(base_sha, str) or not SHA.fullmatch(base_sha):
        raise ServiceError(400, "base_sha must be a full commit SHA")
    parsed = urlsplit(GITLAB_BASE_URL)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ServiceError(503, "GITLAB_BASE_URL must be an HTTPS URL")
    if not GITLAB_WORKSPACE_TOKEN:
        raise ServiceError(503, "GITLAB_WORKSPACE_TOKEN is unavailable")

    with LOCK:
        active = [path for path in WORKSPACES_DIR.glob("ws_*") if path.is_dir()]
        if len(active) >= WORKSPACE_MAX_CONCURRENT:
            raise ServiceError(429, "workspace capacity is full")
        if total_workspace_bytes() >= WORKSPACE_MAX_BYTES:
            raise ServiceError(429, "workspace byte capacity is full")
        workspace_id = f"ws_{uuid.uuid4().hex}"
        directory = workspace_dir(workspace_id)
        repo = directory / "repo"
        repo.mkdir(parents=True)

    encoded_project = "/".join(quote(part, safe="-._~") for part in project.split("/"))
    remote = f"{GITLAB_BASE_URL}/{encoded_project}.git"
    try:
        run_git(["init", "--quiet", "."], repo)
        run_git(["check-ref-format", "--branch", target_branch], repo, max_output=1024)
        run_git(["remote", "add", "origin", remote], repo)
        run_git(
            [
                "fetch", f"--depth={WORKSPACE_FETCH_DEPTH}", "--no-tags", "origin",
                f"+refs/merge-requests/{iid}/head:refs/review/head",
                f"+refs/heads/{target_branch}:refs/review/target",
            ],
            repo,
            timeout=max(SUBPROCESS_TIMEOUT, 120),
            max_output=1_000_000,
            token=True,
        )
        actual_head = run_git(["rev-parse", "refs/review/head"], repo, max_output=1024).decode().strip()
        if actual_head != expected_head_sha:
            raise ServiceError(409, f"REVIEW_STALE: head_sha changed from {expected_head_sha} to {actual_head}")
        run_git(["cat-file", "-e", f"{base_sha}^{{commit}}"], repo, max_output=1024)
        run_git(["checkout", "--quiet", "--detach", "refs/review/head"], repo, timeout=60, max_output=1_000_000)
        size_bytes = directory_size(directory)
        with LOCK:
            if size_bytes > WORKSPACE_MAX_BYTES or total_workspace_bytes(exclude=directory) + size_bytes > WORKSPACE_MAX_BYTES:
                raise ServiceError(429, "workspace byte capacity would be exceeded")
        timestamp = datetime.now(timezone.utc)
        meta = {
            "workspace_id": workspace_id,
            "review_id": review_id,
            "project": project,
            "merge_request_iid": iid,
            "head_sha": actual_head,
            "base_sha": base_sha,
            "target_branch": target_branch,
            "created_at": timestamp.isoformat(timespec="seconds"),
            "expires_at": (timestamp + timedelta(seconds=WORKSPACE_TTL_SECONDS)).isoformat(timespec="seconds"),
            "history_truncated": (repo / ".git" / "shallow").is_file(),
            "fetch_depth": WORKSPACE_FETCH_DEPTH,
            "size_bytes": size_bytes,
        }
        write_json(directory / "meta.json", meta)
        with LOCK:
            record = load_review(review_id)
            record["workspace_id"] = workspace_id
            record["workspace_status"] = "active"
            save_review(record)
        return meta
    except Exception:
        if directory.exists():
            trashed = TRASH_DIR / f"{workspace_id}-failed-{uuid.uuid4().hex}"
            try:
                directory.replace(trashed)
                shutil.rmtree(trashed, ignore_errors=True)
            except OSError:
                shutil.rmtree(directory, ignore_errors=True)
        raise


def workspace_repo(workspace_id: str, *, search: bool = False) -> tuple[Path, dict[str, Any]]:
    directory, meta = load_workspace(workspace_id)
    reserve_call(meta, search=search)
    return directory / "repo", meta


def list_files(workspace_id: str, query: dict[str, list[str]]) -> dict[str, Any]:
    repo, meta = workspace_repo(workspace_id)
    prefix = query.get("path", [""])[0].strip("/")
    max_entries = parse_integer(query, "max_entries", 500, 1, 1000)
    arguments = ["ls-files", "-z"]
    if prefix:
        safe_repository_path(repo, prefix)
        arguments.extend(("--", prefix))
    raw = run_git(arguments, repo, max_output=8_000_000)
    names = [item.decode("utf-8", "replace") for item in raw.split(b"\0") if item]
    return {
        "workspace_id": workspace_id,
        "path": prefix,
        "entries": names[:max_entries],
        "entries_total": len(names),
        "truncated": len(names) > max_entries,
        "head_sha": meta["head_sha"],
    }


def read_file(workspace_id: str, query: dict[str, list[str]]) -> dict[str, Any]:
    repo, meta = workspace_repo(workspace_id)
    candidate = required_query(query, "path")
    start_line = parse_integer(query, "start_line", 1, 1, 10_000_000)
    max_lines = parse_integer(query, "max_lines", 500, 1, MAX_FILE_LINES)
    if secret_path(candidate):
        return {"workspace_id": workspace_id, "path": candidate, "content_withheld": True}
    resolved = safe_repository_path(repo, candidate)
    try:
        if resolved.is_symlink() or not resolved.is_file():
            raise ServiceError(404, "file not found")
        with resolved.open("rb") as stream:
            sample = stream.read(8192)
            if b"\0" in sample:
                return {"workspace_id": workspace_id, "path": candidate, "binary": True, "content_withheld": True}
            stream.seek(0)
            selected: list[list[Any]] = []
            total_lines = 0
            response_chars = 0
            truncated = False
            for number, raw in enumerate(stream, 1):
                total_lines = number
                if number < start_line:
                    continue
                if len(selected) >= max_lines or response_chars >= 500_000:
                    truncated = True
                    continue
                text = raw.decode("utf-8", "replace").rstrip("\r\n")
                if len(text) > 4000:
                    text = text[:4000] + "…"
                response_chars += len(text)
                selected.append([number, text])
    except OSError as error:
        raise ServiceError(404, "file not found") from error
    return {
        "workspace_id": workspace_id,
        "path": candidate,
        "head_sha": meta["head_sha"],
        "line_format": ["line", "text"],
        "lines": selected,
        "start_line": start_line,
        "lines_returned": len(selected),
        "total_lines": total_lines,
        "truncated": truncated or (selected and selected[-1][0] < total_lines),
        "content_withheld": False,
    }


def search_workspace(workspace_id: str, query: dict[str, list[str]]) -> dict[str, Any]:
    repo, meta = workspace_repo(workspace_id, search=True)
    pattern = required_query(query, "q")
    if len(pattern) > 500 or "\x00" in pattern or "\n" in pattern:
        raise ServiceError(400, "q must be one line of at most 500 characters")
    path = query.get("path", [""])[0].strip("/")
    max_matches = parse_integer(query, "max_matches", 100, 1, MAX_SEARCH_MATCHES)
    if path and secret_path(path):
        return {
            "workspace_id": workspace_id,
            "head_sha": meta["head_sha"],
            "matches": [],
            "matches_returned": 0,
            "truncated": False,
            "content_withheld": True,
        }
    arguments = ["grep", "-n", "-I", "--full-name", "-e", pattern]
    if path:
        safe_repository_path(repo, path)
        arguments.extend(("--", path))
    # git grep uses status 1 for a clean no-match result.
    raw = run_git(arguments, repo, max_output=16_000_000, allowed_returncodes=(0, 1))
    matches = []
    withheld = 0
    for raw_line in raw.decode("utf-8", "replace").splitlines():
        match = re.match(r"^(.*):(\d+):(.*)$", raw_line)
        if not match:
            continue
        if secret_path(match.group(1)):
            withheld += 1
            continue
        matches.append({"path": match.group(1), "line": int(match.group(2)), "text": match.group(3)[:1000]})
    return {
        "workspace_id": workspace_id,
        "head_sha": meta["head_sha"],
        "matches": matches[:max_matches],
        "matches_returned": min(len(matches), max_matches),
        "truncated": len(matches) > max_matches,
        "content_withheld_matches": withheld,
    }


def changed_files(repo: Path, base_sha: str, head_sha: str) -> list[dict[str, Any]]:
    raw = run_git(["diff", "--name-status", "-z", f"{base_sha}..{head_sha}"], repo, max_output=16_000_000)
    fields = [field.decode("utf-8", "replace") for field in raw.split(b"\0") if field]
    entries: list[dict[str, Any]] = []
    index = 0
    while index < len(fields):
        status = fields[index]
        index += 1
        if not status:
            continue
        kind = status[0]
        if kind in {"R", "C"}:
            if index + 1 >= len(fields):
                raise ServiceError(500, "git returned an invalid changed-file inventory")
            old_path, new_path = fields[index], fields[index + 1]
            index += 2
        else:
            if index >= len(fields):
                raise ServiceError(500, "git returned an invalid changed-file inventory")
            old_path = new_path = fields[index]
            index += 1
            if kind == "A":
                old_path = new_path
            elif kind == "D":
                new_path = old_path
        entries.append({"status": status, "old_path": old_path, "new_path": new_path})
    return entries


def parse_patch(patch: str, max_lines: int, remaining: int) -> tuple[list[list[Any]], int]:
    lines: list[list[Any]] = []
    dropped = 0
    old_number = new_number = 0
    for raw in (patch[:-1] if patch.endswith("\n") else patch).split("\n"):
        if raw.startswith("\\"):
            continue
        hunk = HUNK.match(raw)
        if hunk:
            old_number, new_number = int(hunk.group(1)), int(hunk.group(3))
            continue
        if old_number == 0 and new_number == 0:
            continue
        marker = raw[:1] if raw else " "
        text = raw[1:] if raw else ""
        if marker not in {"+", "-", " "}:
            continue
        if len(lines) >= max_lines or len(lines) >= remaining:
            dropped += 1
        elif marker == "+":
            lines.append(["add", None, new_number, text])
        elif marker == "-":
            lines.append(["del", old_number, None, text])
        else:
            lines.append(["ctx", old_number, new_number, text])
        if marker != "+":
            old_number += 1
        if marker != "-":
            new_number += 1
    return lines, dropped


def workspace_diff(workspace_id: str, query: dict[str, list[str]]) -> dict[str, Any]:
    repo, meta = workspace_repo(workspace_id)
    requested_value = query.get("paths", [""])[0]
    requested = [path.strip() for path in requested_value.split(",") if path.strip()]
    if len(requested) > 100 or len(requested_value) > 20_000:
        raise ServiceError(400, "paths must name at most 100 files")
    max_per_file = parse_integer(query, "max_lines_per_file", 400, 20, 2000)
    max_total = parse_integer(query, "max_total_lines", 4000, 100, 20_000)
    inventory = changed_files(repo, meta["base_sha"], meta["head_sha"])
    if requested:
        requested_set = set(requested)
        selected = [entry for entry in inventory if {entry["old_path"], entry["new_path"]} & requested_set]
        found = {path for entry in selected for path in (entry["old_path"], entry["new_path"])} & requested_set
        paths_not_found = [path for path in requested if path not in found]
    else:
        if len(inventory) > 100:
            raise ServiceError(400, "paths is required when more than 100 files changed")
        selected = inventory
        paths_not_found = []
    files = []
    total_lines = total_dropped = 0
    for entry in selected:
        if secret_path(entry["old_path"]) or secret_path(entry["new_path"]):
            files.append({
                **entry,
                "lines": [],
                "lines_returned": 0,
                "lines_dropped": 0,
                "truncated": False,
                "binary": False,
                "content_withheld": True,
            })
            continue
        path_arguments = [entry["old_path"]]
        if entry["new_path"] != entry["old_path"]:
            path_arguments.append(entry["new_path"])
        raw = run_git(
            ["diff", "--no-ext-diff", "--no-textconv", "--unified=3", f"{meta['base_sha']}..{meta['head_sha']}", "--", *path_arguments],
            repo,
        )
        patch = raw.decode("utf-8", "replace")
        lines, dropped = parse_patch(patch, max_per_file, max_total - total_lines)
        binary = bool(patch) and not lines and ("Binary files " in patch or "GIT binary patch" in patch)
        file_entry = {
            **entry,
            "lines": lines,
            "lines_returned": len(lines),
            "lines_dropped": dropped,
            "truncated": dropped > 0,
            "binary": binary,
            "content_withheld": False,
        }
        files.append(file_entry)
        total_lines += len(lines)
        total_dropped += dropped
    files_withheld = [entry["new_path"] for entry in files if entry["content_withheld"]]
    complete = not paths_not_found and total_dropped == 0 and not files_withheld
    return {
        "workspace_id": workspace_id,
        "head_sha": meta["head_sha"],
        "base_sha": meta["base_sha"],
        "line_format": ["type", "old_line", "new_line", "text"],
        "files": files,
        "coverage": {
            "files_total": len(inventory),
            "files_returned": len(files),
            "files_truncated": [entry["new_path"] for entry in files if entry["truncated"]],
            "files_withheld": files_withheld,
            "lines_returned": total_lines,
            "lines_dropped": total_dropped,
            "paths_requested": requested,
            "paths_not_found": paths_not_found,
            "complete": complete,
        },
    }


def blame_file(workspace_id: str, query: dict[str, list[str]]) -> dict[str, Any]:
    repo, meta = workspace_repo(workspace_id)
    candidate = required_query(query, "path")
    start_line = parse_integer(query, "start_line", 1, 1, 10_000_000)
    end_line = parse_integer(query, "end_line", start_line + 99, start_line, start_line + 499)
    if secret_path(candidate):
        return {"workspace_id": workspace_id, "path": candidate, "content_withheld": True, "history_truncated": meta["history_truncated"]}
    resolved = safe_repository_path(repo, candidate)
    if not resolved.is_file() or resolved.is_symlink():
        raise ServiceError(404, "file not found")
    raw = run_git(
        ["blame", "--line-porcelain", "-L", f"{start_line},{end_line}", meta["head_sha"], "--", candidate],
        repo,
        max_output=8_000_000,
    )
    entries = []
    current: dict[str, Any] | None = None
    for line in raw.decode("utf-8", "replace").splitlines():
        header = re.match(r"^([0-9a-f]{40,64}) (\d+) (\d+)(?: (\d+))?$", line)
        if header:
            current = {"commit": header.group(1), "original_line": int(header.group(2)), "line": int(header.group(3))}
        elif current is not None and line.startswith("author "):
            current["author"] = line[7:][:200]
        elif current is not None and line.startswith("author-time "):
            try:
                current["author_time"] = int(line[12:])
            except ValueError:
                current["author_time"] = None
        elif current is not None and line.startswith("\t"):
            current["text"] = line[1:][:4000]
            entries.append(current)
            current = None
    return {
        "workspace_id": workspace_id,
        "path": candidate,
        "head_sha": meta["head_sha"],
        "history_truncated": meta["history_truncated"],
        "lines": entries,
    }


def required_query(query: dict[str, list[str]], name: str) -> str:
    value = query.get(name, [""])[0]
    if not value:
        raise ServiceError(400, f"{name} is required")
    return value


def parse_integer(query: dict[str, list[str]], name: str, default: int, minimum: int, maximum: int) -> int:
    raw = query.get(name, [str(default)])[0]
    try:
        value = int(raw)
    except (TypeError, ValueError) as error:
        raise ServiceError(400, f"{name} must be an integer") from error
    if not minimum <= value <= maximum:
        raise ServiceError(400, f"{name} must be from {minimum} through {maximum}")
    return value


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, message: str, *args: object) -> None:
        print(f"review: {self.client_address[0]} - {message % args}", flush=True)

    def send_json(self, status: int, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def authorized(self) -> bool:
        supplied = self.headers.get("Authorization", "")
        expected = f"Bearer {API_KEY}"
        return bool(API_KEY) and hmac.compare_digest(supplied.encode(), expected.encode())

    def body_json(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise ServiceError(400, "Content-Length is invalid") from error
        if length < 1 or length > MAX_REQUEST_BYTES:
            raise ServiceError(413 if length > MAX_REQUEST_BYTES else 400, "request body size is invalid")
        try:
            payload = json.loads(self.rfile.read(length))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ServiceError(400, "request body must be a JSON object") from error
        if not isinstance(payload, dict):
            raise ServiceError(400, "request body must be a JSON object")
        return payload

    def dispatch(self) -> None:
        parsed = urlsplit(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query, keep_blank_values=True)
        if self.command == "GET" and path == "/health":
            self.send_json(200, {"status": True, "git": shutil.which("git") is not None})
            return
        if not self.authorized():
            self.send_json(401, {"error": "unauthorized"})
            return

        if self.command == "POST" and path == "/reviews":
            self.send_json(201, create_review(self.body_json()))
            return
        match = re.fullmatch(r"/reviews/(rev_[0-9a-f]{32})", path)
        if self.command == "GET" and match:
            self.send_json(200, load_review(match.group(1)))
            return
        match = re.fullmatch(r"/reviews/(rev_[0-9a-f]{32})/events", path)
        if match and self.command == "POST":
            payload = self.body_json()
            self.send_json(201, append_event(match.group(1), payload.get("stage"), payload.get("detail")))
            return
        if match and self.command == "GET":
            after = parse_integer(query, "after", 0, 0, 2_147_483_647)
            events = []
            event_path = review_dir(match.group(1)) / "events.jsonl"
            load_review(match.group(1))
            try:
                for line in event_path.read_text(encoding="utf-8").splitlines():
                    event = json.loads(line)
                    if isinstance(event, dict) and int(event.get("cursor", 0)) > after:
                        events.append(event)
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as error:
                raise ServiceError(500, "review event log is unreadable") from error
            events = events[:100]
            self.send_json(200, {"events": events, "cursor": events[-1]["cursor"] if events else after})
            return
        match = re.fullmatch(r"/reviews/(rev_[0-9a-f]{32})/close", path)
        if self.command == "POST" and match:
            payload = self.body_json()
            reason = payload.get("reason", "completed")
            if not isinstance(reason, str):
                raise ServiceError(400, "reason must be a string")
            self.send_json(200, close_review(match.group(1), reason))
            return
        if self.command == "POST" and path == "/workspaces":
            self.send_json(201, create_workspace(self.body_json()))
            return
        match = re.fullmatch(r"/workspaces/(ws_[0-9a-f]{32})", path)
        if self.command == "DELETE" and match:
            discarded = discard_workspace(match.group(1))
            self.send_json(200, {"workspace_id": match.group(1), "discarded": discarded})
            return
        match = re.fullmatch(r"/workspaces/(ws_[0-9a-f]{32})/(tree|file|search|diff|blame)", path)
        if self.command == "GET" and match:
            workspace_id, operation = match.groups()
            functions = {
                "tree": list_files,
                "file": read_file,
                "search": search_workspace,
                "diff": workspace_diff,
                "blame": blame_file,
            }
            self.send_json(200, functions[operation](workspace_id, query))
            return
        raise ServiceError(404, "not found")

    def handle_request(self) -> None:
        try:
            self.dispatch()
        except ServiceError as error:
            self.send_json(error.status, {"error": error.message})
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as error:
            print(f"review: request failed ({type(error).__name__})", flush=True)
            if not self.wfile.closed:
                self.send_json(500, {"error": "internal service error"})

    def do_GET(self) -> None:  # noqa: N802
        self.handle_request()

    def do_POST(self) -> None:  # noqa: N802
        self.handle_request()

    def do_DELETE(self) -> None:  # noqa: N802
        self.handle_request()


def main() -> None:
    for directory in (DATA_DIR, REVIEWS_DIR, WORKSPACES_DIR, TRASH_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    cleanup_once()
    reaper = threading.Thread(target=cleanup_loop, name="review-reaper", daemon=True)
    reaper.start()
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"review service listening on 0.0.0.0:{PORT}", flush=True)
    try:
        server.serve_forever()
    finally:
        STOP_EVENT.set()
        server.server_close()


if __name__ == "__main__":
    main()
