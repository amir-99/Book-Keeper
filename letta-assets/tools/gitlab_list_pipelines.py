def gitlab_list_pipelines(
    project_id: str, status: str = "", ref: str = "", source: str = "",
    limit: int = 50, page: int = 1
) -> str:
    """List pipelines in one GitLab project.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        status: Optional exact GitLab pipeline status.
        ref: Optional branch or tag filter.
        source: Optional pipeline source filter, such as push or merge_request_event.
        limit: Results per page, from 1 through 100.
        page: One-based result page.

    Returns:
        JSON containing compact pipeline metadata, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    allowed_statuses = {"", "created", "waiting_for_resource", "preparing", "pending", "running", "success", "failed", "canceled", "skipped", "manual", "scheduled"}
    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    if status not in allowed_statuses:
        return "GITLAB_ERROR: unsupported pipeline status"
    for name, value, maximum in (("ref", ref, 1000), ("source", source, 100)):
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
    params = {"order_by": "updated_at", "sort": "desc", "per_page": limit, "page": page}
    for key, value in (("status", status), ("ref", ref), ("source", source)):
        if value.strip():
            params[key] = value.strip()
    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/pipelines?{urlencode(params)}",
        headers={"Accept": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while listing pipelines"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to list pipelines ({type(error).__name__})"
    if not isinstance(payload, list):
        return "GITLAB_ERROR: GitLab returned an invalid pipeline list"
    keys = ("id", "iid", "project_id", "sha", "ref", "status", "source", "created_at", "updated_at", "web_url", "name")
    pipelines = [{key: item.get(key) for key in keys} for item in payload if isinstance(item, dict)]
    return json.dumps({"pipelines": pipelines, "page": page, "limit": limit, "returned": len(pipelines)}, ensure_ascii=False)
