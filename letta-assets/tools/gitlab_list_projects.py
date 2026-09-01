def gitlab_list_projects(
    search: str = "", limit: int = 50, page: int = 1, include_archived: bool = False
) -> str:
    """List GitLab projects visible to the configured access token.

    Args:
        search: Optional project name or path fragment.
        limit: Results per page, from 1 through 100.
        page: One-based result page.
        include_archived: Include archived projects when true.

    Returns:
        JSON containing compact project metadata, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(search, str) or len(search) > 500:
        return "GITLAB_ERROR: search must be a string up to 500 characters"
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        return "GITLAB_ERROR: limit must be an integer from 1 through 100"
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        return "GITLAB_ERROR: page must be a positive integer"
    if not isinstance(include_archived, bool):
        return "GITLAB_ERROR: include_archived must be a boolean"

    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"

    params = {
        "membership": "true",
        "simple": "true",
        "order_by": "last_activity_at",
        "sort": "desc",
        "per_page": limit,
        "page": page,
    }
    if search.strip():
        params["search"] = search.strip()
    if not include_archived:
        params["archived"] = "false"
    request = Request(
        f"{base_url}/api/v4/projects?{urlencode(params)}",
        headers={"Accept": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while listing projects"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to list projects ({type(error).__name__})"
    if not isinstance(payload, list):
        return "GITLAB_ERROR: GitLab returned an invalid project list"

    projects = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        namespace = item.get("namespace") if isinstance(item.get("namespace"), dict) else {}
        projects.append({
            "id": item.get("id"),
            "name": item.get("name"),
            "path_with_namespace": item.get("path_with_namespace"),
            "namespace": namespace.get("full_path") or namespace.get("name"),
            "default_branch": item.get("default_branch"),
            "visibility": item.get("visibility"),
            "archived": item.get("archived"),
            "last_activity_at": item.get("last_activity_at"),
            "web_url": item.get("web_url"),
        })
    return json.dumps({"projects": projects, "page": page, "limit": limit, "returned": len(projects)}, ensure_ascii=False)
