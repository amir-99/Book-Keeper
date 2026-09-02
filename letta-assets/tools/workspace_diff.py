def workspace_diff(
    workspace_id: str, paths: str = "", max_lines_per_file: int = 400,
    max_total_lines: int = 4000,
) -> str:
    """Read code-computed, anchorable diff lines from a pinned workspace.

    This is the workspace tool whose old_line and new_line values may anchor a
    finding. It computes those values from the patch; callers never count hunks.

    Args:
        workspace_id: Workspace identifier returned by workspace_open.
        paths: Comma-separated changed paths; required for changes above 100 files.
        max_lines_per_file: Lines kept per file, from 20 through 2000.
        max_total_lines: Lines kept across the response, from 100 through 20000.

    Returns:
        JSON containing numbered diff entries and computed coverage, or a
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
    if not isinstance(paths, str) or len(paths) > 20000 or len([item for item in paths.split(",") if item.strip()]) > 100:
        return "REVIEW_ERROR: paths must name at most 100 files in 20000 characters"
    if not isinstance(max_lines_per_file, int) or isinstance(max_lines_per_file, bool) or not 20 <= max_lines_per_file <= 2000:
        return "REVIEW_ERROR: max_lines_per_file must be from 20 through 2000"
    if not isinstance(max_total_lines, int) or isinstance(max_total_lines, bool) or not 100 <= max_total_lines <= 20000:
        return "REVIEW_ERROR: max_total_lines must be from 100 through 20000"
    base_url = os.getenv("REVIEW_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("REVIEW_API_KEY", "")
    if not base_url or not api_key:
        return "REVIEW_ERROR: review service configuration is unavailable"
    query = urlencode({"paths": paths, "max_lines_per_file": max_lines_per_file, "max_total_lines": max_total_lines})
    request = Request(
        f"{base_url}/workspaces/{quote(workspace_id, safe='')}/diff?{query}",
        headers={"Accept": "application/json", "Authorization": f"Bearer {api_key}", "User-Agent": "letta-review-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=60) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        try:
            failure = json.loads(error.read(4096)).get("error")
        except (ValueError, json.JSONDecodeError, AttributeError):
            failure = None
        return f"REVIEW_ERROR: {failure}" if isinstance(failure, str) else f"REVIEW_ERROR: review service returned HTTP {error.code} while reading the diff"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"REVIEW_ERROR: unable to read the workspace diff ({type(error).__name__})"
    if not isinstance(payload, dict) or not isinstance(payload.get("files"), list) or not isinstance(payload.get("coverage"), dict):
        return "REVIEW_ERROR: review service returned an unexpected diff payload"
    return json.dumps(payload, ensure_ascii=False)

