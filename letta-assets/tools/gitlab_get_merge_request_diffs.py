def gitlab_get_merge_request_diffs(
    project_id: str, merge_request_iid: int, limit: int = 100, page: int = 1,
    max_patch_chars: int = 30000
) -> str:
    """Read bounded per-file diffs for one GitLab merge request.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        merge_request_iid: Positive project-scoped merge-request IID.
        limit: Diff files per page, from 1 through 100.
        page: One-based result page.
        max_patch_chars: Maximum patch characters per file, from 1000 through 100000.

    Returns:
        JSON containing per-file diff metadata and patches, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    if not isinstance(merge_request_iid, int) or isinstance(merge_request_iid, bool) or merge_request_iid < 1:
        return "GITLAB_ERROR: merge_request_iid must be a positive integer"
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        return "GITLAB_ERROR: limit must be an integer from 1 through 100"
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        return "GITLAB_ERROR: page must be a positive integer"
    if not isinstance(max_patch_chars, int) or isinstance(max_patch_chars, bool) or not 1000 <= max_patch_chars <= 100000:
        return "GITLAB_ERROR: max_patch_chars must be from 1000 through 100000"

    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"
    encoded_project = quote(project_id.strip(), safe="")
    query = urlencode({"per_page": limit, "page": page})
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/merge_requests/{merge_request_iid}/diffs?{query}",
        headers={"Accept": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while reading merge-request diffs"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to read merge-request diffs ({type(error).__name__})"
    if not isinstance(payload, list):
        return "GITLAB_ERROR: GitLab returned invalid merge-request diffs"
    diffs = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        patch = item.get("diff") if isinstance(item.get("diff"), str) else ""
        truncated = len(patch) > max_patch_chars
        if truncated:
            patch = patch[:max_patch_chars] + "\n[TRUNCATED]"
        result = {key: item.get(key) for key in ("old_path", "new_path", "a_mode", "b_mode", "new_file", "renamed_file", "deleted_file", "generated_file", "collapsed", "too_large")}
        result.update({"diff": patch, "truncated": truncated})
        diffs.append(result)
    return json.dumps({"merge_request_iid": merge_request_iid, "diffs": diffs, "page": page, "limit": limit, "returned": len(diffs)}, ensure_ascii=False)
