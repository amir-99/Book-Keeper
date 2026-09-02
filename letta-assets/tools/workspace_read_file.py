def workspace_read_file(
    workspace_id: str, path: str, start_line: int = 1, max_lines: int = 500,
) -> str:
    """Read bounded text lines from one non-secret file in a pinned workspace.

    Line numbers returned here are context only and are never valid review
    anchors; only workspace_diff and the GitLab diff tool return anchorable lines.

    Args:
        workspace_id: Workspace identifier returned by workspace_open.
        path: Repository-relative file path.
        start_line: One-based first line to return.
        max_lines: Maximum lines to return, from 1 through 2000.

    Returns:
        JSON with bounded numbered text or a content_withheld marker, or a
        concise REVIEW_ERROR string.
    """
    import json
    import os
    import re
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode
    from urllib.request import Request, urlopen

    if not isinstance(workspace_id, str) or not re.fullmatch(r"ws_[0-9a-f]{32}", workspace_id):
        return "REVIEW_ERROR: workspace_id must be an identifier from workspace_open"
    if not isinstance(path, str) or not path or len(path) > 4096:
        return "REVIEW_ERROR: path must be a non-empty string up to 4096 characters"
    if not isinstance(start_line, int) or isinstance(start_line, bool) or start_line < 1:
        return "REVIEW_ERROR: start_line must be a positive integer"
    if not isinstance(max_lines, int) or isinstance(max_lines, bool) or not 1 <= max_lines <= 2000:
        return "REVIEW_ERROR: max_lines must be from 1 through 2000"
    base_url = os.getenv("REVIEW_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("REVIEW_API_KEY", "")
    if not base_url or not api_key:
        return "REVIEW_ERROR: review service configuration is unavailable"
    query = urlencode({"path": path, "start_line": start_line, "max_lines": max_lines})
    request = Request(
        f"{base_url}/workspaces/{quote(workspace_id, safe='')}/file?{query}",
        headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}", "User-Agent": "letta-review-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        try:
            failure = json.loads(error.read(4096)).get("error")
        except (ValueError, json.JSONDecodeError, AttributeError):
            failure = None
        return f"REVIEW_ERROR: {failure}" if isinstance(failure, str) else f"REVIEW_ERROR: review service returned HTTP {error.code} while reading the file"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"REVIEW_ERROR: unable to read the workspace file ({type(error).__name__})"
    if not isinstance(payload, dict) or payload.get("path") != path:
        return "REVIEW_ERROR: review service returned an unexpected file payload"
    return json.dumps(payload, ensure_ascii=False)

