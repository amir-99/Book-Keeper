def gitlab_search_project(
    project_id: str, scope: str, search: str, ref: str = "", limit: int = 20, page: int = 1
) -> str:
    """Search one GitLab project within an explicitly selected scope.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        scope: One of blobs, commits, issues, merge_requests, milestones, notes, users, or wiki_blobs.
        search: Search expression. Code search availability depends on the GitLab tier and search configuration.
        ref: Optional repository ref for blob searches.
        limit: Results per page, from 1 through 100.
        page: One-based result page.

    Returns:
        JSON containing bounded search results, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    allowed_scopes = {"blobs", "commits", "issues", "merge_requests", "milestones", "notes", "users", "wiki_blobs"}
    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    if scope not in allowed_scopes:
        return "GITLAB_ERROR: unsupported search scope"
    if not isinstance(search, str) or not search.strip() or len(search) > 2000:
        return "GITLAB_ERROR: search must be a non-empty string up to 2000 characters"
    if not isinstance(ref, str) or len(ref) > 1000:
        return "GITLAB_ERROR: ref must be a string up to 1000 characters"
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        return "GITLAB_ERROR: limit must be an integer from 1 through 100"
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        return "GITLAB_ERROR: page must be a positive integer"

    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"
    params = {"scope": scope, "search": search.strip(), "per_page": limit, "page": page}
    if ref.strip():
        params["ref"] = ref.strip()
    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/search?{urlencode(params)}",
        headers={"Accept": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while searching the project"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to search the project ({type(error).__name__})"
    if not isinstance(payload, list):
        return "GITLAB_ERROR: GitLab returned invalid search results"
    bounded = []
    for raw_item in payload:
        if not isinstance(raw_item, dict):
            continue
        item = dict(raw_item)
        for key, value in item.items():
            if isinstance(value, str) and len(value) > 10000:
                item[key] = value[:10000] + "\n[TRUNCATED]"
        bounded.append(item)
    return json.dumps({"results": bounded, "scope": scope, "page": page, "limit": limit, "returned": len(bounded)}, ensure_ascii=False)
