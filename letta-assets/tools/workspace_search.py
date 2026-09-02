def workspace_search(
    workspace_id: str, query: str, path: str = "", max_matches: int = 100,
) -> str:
    """Search tracked text in a pinned workspace with a fixed-string-safe argument.

    Search line numbers are context only and are never valid review anchors.

    Args:
        workspace_id: Workspace identifier returned by workspace_open.
        query: Text or git-grep pattern to find, up to 500 characters.
        path: Optional repository-relative path scope.
        max_matches: Maximum matches to return, from 1 through 200.

    Returns:
        JSON containing capped matches, or a concise REVIEW_ERROR string.
    """
    import json
    import os
    import re
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode
    from urllib.request import Request, urlopen

    if not isinstance(workspace_id, str) or not re.fullmatch(r"ws_[0-9a-f]{32}", workspace_id):
        return "REVIEW_ERROR: workspace_id must be an identifier from workspace_open"
    if not isinstance(query, str) or not query or len(query) > 500 or "\n" in query:
        return "REVIEW_ERROR: query must be one line from 1 through 500 characters"
    if not isinstance(path, str) or len(path) > 4096:
        return "REVIEW_ERROR: path must be a string up to 4096 characters"
    if not isinstance(max_matches, int) or isinstance(max_matches, bool) or not 1 <= max_matches <= 200:
        return "REVIEW_ERROR: max_matches must be from 1 through 200"
    base_url = os.getenv("REVIEW_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("REVIEW_API_KEY", "")
    if not base_url or not api_key:
        return "REVIEW_ERROR: review service configuration is unavailable"
    encoded = urlencode({"q": query, "path": path, "max_matches": max_matches})
    request = Request(
        f"{base_url}/workspaces/{quote(workspace_id, safe='')}/search?{encoded}",
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
        return f"REVIEW_ERROR: {failure}" if isinstance(failure, str) else f"REVIEW_ERROR: review service returned HTTP {error.code} while searching"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"REVIEW_ERROR: unable to search the workspace ({type(error).__name__})"
    if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
        return "REVIEW_ERROR: review service returned an unexpected search payload"
    return json.dumps(payload, ensure_ascii=False)

