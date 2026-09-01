def gitlab_cancel_pipeline(project_id: str, pipeline_id: int) -> str:
    """Cancel all cancelable jobs in one GitLab pipeline.

    This is a write operation and requires the user's explicit authorization.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        pipeline_id: Positive global pipeline ID.

    Returns:
        JSON containing the canceled pipeline metadata, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    if not isinstance(pipeline_id, int) or isinstance(pipeline_id, bool) or pipeline_id < 1:
        return "GITLAB_ERROR: pipeline_id must be a positive integer"
    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"
    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/pipelines/{pipeline_id}/cancel",
        data=b"", method="POST",
        headers={"Accept": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while canceling the pipeline"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to cancel the pipeline ({type(error).__name__})"
    if not isinstance(payload, dict):
        return "GITLAB_ERROR: GitLab returned an invalid canceled pipeline"
    return json.dumps({key: payload.get(key) for key in ("id", "iid", "project_id", "sha", "ref", "status", "updated_at", "web_url")}, ensure_ascii=False)
