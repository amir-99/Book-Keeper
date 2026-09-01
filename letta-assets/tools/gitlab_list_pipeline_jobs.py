def gitlab_list_pipeline_jobs(
    project_id: str, pipeline_id: int, scopes: str = "", include_retried: bool = False,
    limit: int = 100, page: int = 1
) -> str:
    """List jobs belonging to one GitLab pipeline.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        pipeline_id: Positive global pipeline ID.
        scopes: Optional comma-separated job statuses.
        include_retried: Include retried jobs when true.
        limit: Results per page, from 1 through 100.
        page: One-based result page.

    Returns:
        JSON containing compact job metadata, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    allowed_scopes = {"created", "pending", "running", "failed", "success", "canceled", "skipped", "waiting_for_resource", "manual"}
    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    if not isinstance(pipeline_id, int) or isinstance(pipeline_id, bool) or pipeline_id < 1:
        return "GITLAB_ERROR: pipeline_id must be a positive integer"
    if not isinstance(scopes, str) or len(scopes) > 500:
        return "GITLAB_ERROR: scopes must be a string up to 500 characters"
    scope_values = [value.strip() for value in scopes.split(",") if value.strip()]
    unsupported = sorted(set(scope_values) - allowed_scopes)
    if unsupported:
        return "GITLAB_ERROR: unsupported job scopes: " + ", ".join(unsupported)
    if not isinstance(include_retried, bool):
        return "GITLAB_ERROR: include_retried must be a boolean"
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
    params = [("include_retried", str(include_retried).lower()), ("per_page", limit), ("page", page)]
    params.extend(("scope[]", value) for value in scope_values)
    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/pipelines/{pipeline_id}/jobs?{urlencode(params)}",
        headers={"Accept": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while listing pipeline jobs"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to list pipeline jobs ({type(error).__name__})"
    if not isinstance(payload, list):
        return "GITLAB_ERROR: GitLab returned an invalid job list"
    jobs = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        runner = item.get("runner") if isinstance(item.get("runner"), dict) else {}
        jobs.append({key: item.get(key) for key in ("id", "name", "stage", "status", "ref", "tag", "allow_failure", "created_at", "started_at", "finished_at", "duration", "queued_duration", "web_url", "failure_reason")} | {"runner": runner.get("description")})
    return json.dumps({"pipeline_id": pipeline_id, "jobs": jobs, "page": page, "limit": limit, "returned": len(jobs)}, ensure_ascii=False)
