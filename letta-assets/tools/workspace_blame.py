def workspace_blame(
    workspace_id: str, path: str, start_line: int = 1, end_line: int = 100,
) -> str:
    """Read bounded blame context from the pinned workspace head.

    A response marked history_truncated says only that the shallow boundary was
    reached; it is not evidence that a line was never changed. Blame line
    numbers are context and never valid review anchors.

    Args:
        workspace_id: Workspace identifier returned by workspace_open.
        path: Repository-relative file path.
        start_line: One-based first line to blame.
        end_line: Inclusive final line, at most 499 lines after start_line.

    Returns:
        JSON containing bounded blame entries and history_truncated, or a
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
    if not isinstance(end_line, int) or isinstance(end_line, bool) or not start_line <= end_line <= start_line + 499:
        return "REVIEW_ERROR: end_line must be at or after start_line and span at most 500 lines"
    base_url = os.getenv("REVIEW_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("REVIEW_API_KEY", "")
    if not base_url or not api_key:
        return "REVIEW_ERROR: review service configuration is unavailable"
    query = urlencode({"path": path, "start_line": start_line, "end_line": end_line})
    request = Request(
        f"{base_url}/workspaces/{quote(workspace_id, safe='')}/blame?{query}",
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
        return f"REVIEW_ERROR: {failure}" if isinstance(failure, str) else f"REVIEW_ERROR: review service returned HTTP {error.code} while reading blame"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"REVIEW_ERROR: unable to read workspace blame ({type(error).__name__})"
    if not isinstance(payload, dict) or not isinstance(payload.get("history_truncated"), bool):
        return "REVIEW_ERROR: review service returned an unexpected blame payload"
    return json.dumps(payload, ensure_ascii=False)

