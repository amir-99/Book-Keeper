def gitlab_list_merge_requests(
    project_id: str, state: str = "opened", search: str = "", source_branch: str = "",
    target_branch: str = "", limit: int = 50, page: int = 1
) -> str:
    """List or search merge requests in one GitLab project.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        state: opened, closed, locked, merged, or all.
        search: Optional text matched against title and description.
        source_branch: Optional exact source branch filter.
        target_branch: Optional exact target branch filter.
        limit: Results per page, from 1 through 100.
        page: One-based result page.

    Returns:
        JSON containing compact merge-request metadata, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    if state not in {"opened", "closed", "locked", "merged", "all"}:
        return "GITLAB_ERROR: state must be opened, closed, locked, merged, or all"
    for name, value, maximum in (("search", search, 2000), ("source_branch", source_branch, 1000), ("target_branch", target_branch, 1000)):
        if not isinstance(value, str) or len(value) > maximum:
            return f"GITLAB_ERROR: {name} must be a string up to {maximum} characters"
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
    params = {"scope": "all", "state": state, "order_by": "updated_at", "sort": "desc", "per_page": limit, "page": page}
    for key, value in (("search", search), ("source_branch", source_branch), ("target_branch", target_branch)):
        if value.strip():
            params[key] = value.strip()
    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/merge_requests?{urlencode(params)}",
        headers={"Accept": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while listing merge requests"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to list merge requests ({type(error).__name__})"
    if not isinstance(payload, list):
        return "GITLAB_ERROR: GitLab returned an invalid merge-request list"
    merge_requests = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        assignees = item.get("assignees") if isinstance(item.get("assignees"), list) else []
        reviewers = item.get("reviewers") if isinstance(item.get("reviewers"), list) else []
        merge_requests.append({
            "id": item.get("id"), "iid": item.get("iid"), "title": item.get("title"),
            "state": item.get("state"), "draft": item.get("draft"),
            "source_branch": item.get("source_branch"), "target_branch": item.get("target_branch"),
            "author": author.get("username"),
            "assignees": [value.get("username") for value in assignees if isinstance(value, dict)],
            "reviewers": [value.get("username") for value in reviewers if isinstance(value, dict)],
            "detailed_merge_status": item.get("detailed_merge_status"), "has_conflicts": item.get("has_conflicts"),
            "created_at": item.get("created_at"), "updated_at": item.get("updated_at"),
            "merged_at": item.get("merged_at"), "web_url": item.get("web_url"),
        })
    return json.dumps({"merge_requests": merge_requests, "state": state, "page": page, "limit": limit, "returned": len(merge_requests)}, ensure_ascii=False)
