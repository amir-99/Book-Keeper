def workspace_list_files(workspace_id: str, path: str = "", max_entries: int = 500) -> str:
    """List tracked files in a pinned review workspace.

    Args:
        workspace_id: Workspace identifier returned by workspace_open.
        path: Optional repository-relative directory or path prefix.
        max_entries: Maximum entries to return, from 1 through 1000.

    Returns:
        JSON containing a bounded tracked-file list, or a concise REVIEW_ERROR string.
    """
    import json
    import os
    import re
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode
    from urllib.request import Request, urlopen

    if not isinstance(workspace_id, str) or not re.fullmatch(r"ws_[0-9a-f]{32}", workspace_id):
        return "REVIEW_ERROR: workspace_id must be an identifier from workspace_open"
    if not isinstance(path, str) or len(path) > 4096:
        return "REVIEW_ERROR: path must be a string up to 4096 characters"
    if not isinstance(max_entries, int) or isinstance(max_entries, bool) or not 1 <= max_entries <= 1000:
        return "REVIEW_ERROR: max_entries must be from 1 through 1000"
    base_url = os.getenv("REVIEW_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("REVIEW_API_KEY", "")
    if not base_url or not api_key:
        return "REVIEW_ERROR: review service configuration is unavailable"
    query = urlencode({"path": path, "max_entries": max_entries})
    request = Request(
        f"{base_url}/workspaces/{quote(workspace_id, safe='')}/tree?{query}",
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
        return f"REVIEW_ERROR: {failure}" if isinstance(failure, str) else f"REVIEW_ERROR: review service returned HTTP {error.code} while listing files"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"REVIEW_ERROR: unable to list workspace files ({type(error).__name__})"
    if not isinstance(payload, dict) or not isinstance(payload.get("entries"), list):
        return "REVIEW_ERROR: review service returned an unexpected file list"
    return json.dumps(payload, ensure_ascii=False)

