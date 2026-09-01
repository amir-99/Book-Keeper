def gitlab_list_commits(
    project_id: str, ref: str = "HEAD", path: str = "", since: str = "", until: str = "",
    limit: int = 50, page: int = 1
) -> str:
    """List commits in a GitLab repository with optional ref, path, and time filters.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        ref: Branch, tag, or revision range; HEAD selects the default branch.
        path: Optional repository path filter.
        since: Optional ISO 8601 lower timestamp bound.
        until: Optional ISO 8601 upper timestamp bound.
        limit: Results per page, from 1 through 100.
        page: One-based result page.

    Returns:
        JSON containing compact commit metadata, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    for name, value, maximum in (("ref", ref, 1000), ("path", path, 4000), ("since", since, 100), ("until", until, 100)):
        if not isinstance(value, str) or len(value) > maximum:
            return f"GITLAB_ERROR: {name} must be a string up to {maximum} characters"
    if not ref.strip():
        return "GITLAB_ERROR: ref must not be empty"
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
    params = {"ref_name": ref.strip(), "per_page": limit, "page": page}
    for key, value in (("path", path), ("since", since), ("until", until)):
        if value.strip():
            params[key] = value.strip()
    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/repository/commits?{urlencode(params)}",
        headers={"Accept": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while listing commits"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to list commits ({type(error).__name__})"
    if not isinstance(payload, list):
        return "GITLAB_ERROR: GitLab returned an invalid commit list"
    keys = ("id", "short_id", "title", "author_name", "author_email", "authored_date", "committer_name", "committed_date", "created_at", "web_url")
    commits = [{key: item.get(key) for key in keys} for item in payload if isinstance(item, dict)]
    return json.dumps({"commits": commits, "ref": ref.strip(), "page": page, "limit": limit, "returned": len(commits)}, ensure_ascii=False)
