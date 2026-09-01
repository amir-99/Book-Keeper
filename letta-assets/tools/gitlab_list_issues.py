def gitlab_list_issues(
    project_id: str, state: str = "opened", search: str = "", labels: str = "",
    limit: int = 50, page: int = 1
) -> str:
    """List or search issues in one GitLab project.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        state: opened, closed, or all.
        search: Optional text matched against issue title and description.
        labels: Optional comma-separated labels; GitLab requires all of them.
        limit: Results per page, from 1 through 100.
        page: One-based result page.

    Returns:
        JSON containing compact issue metadata, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    if state not in {"opened", "closed", "all"}:
        return "GITLAB_ERROR: state must be opened, closed, or all"
    if not isinstance(search, str) or len(search) > 2000:
        return "GITLAB_ERROR: search must be a string up to 2000 characters"
    if not isinstance(labels, str) or len(labels) > 2000:
        return "GITLAB_ERROR: labels must be a string up to 2000 characters"
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
    if search.strip():
        params["search"] = search.strip()
    if labels.strip():
        params["labels"] = labels.strip()
    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/issues?{urlencode(params)}",
        headers={"Accept": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while listing issues"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to list issues ({type(error).__name__})"
    if not isinstance(payload, list):
        return "GITLAB_ERROR: GitLab returned an invalid issue list"
    issues = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        assignees = item.get("assignees") if isinstance(item.get("assignees"), list) else []
        issues.append({
            "id": item.get("id"), "iid": item.get("iid"), "title": item.get("title"),
            "state": item.get("state"), "issue_type": item.get("issue_type"),
            "confidential": item.get("confidential"), "labels": item.get("labels"),
            "author": author.get("username"),
            "assignees": [value.get("username") for value in assignees if isinstance(value, dict)],
            "created_at": item.get("created_at"), "updated_at": item.get("updated_at"),
            "closed_at": item.get("closed_at"), "due_date": item.get("due_date"),
            "web_url": item.get("web_url"),
        })
    return json.dumps({"issues": issues, "state": state, "page": page, "limit": limit, "returned": len(issues)}, ensure_ascii=False)
