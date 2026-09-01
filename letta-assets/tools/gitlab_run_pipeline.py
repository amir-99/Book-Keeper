def gitlab_run_pipeline(project_id: str, ref: str, variables_json: str = "[]") -> str:
    """Create a new GitLab pipeline for one ref.

    This is a write operation and requires the user's explicit authorization.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        ref: Branch or tag to run.
        variables_json: JSON array of objects containing key, value, and an
            optional variable_type of env_var or file.

    Returns:
        JSON containing the created pipeline metadata, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    if not isinstance(ref, str) or not ref.strip() or len(ref) > 1000:
        return "GITLAB_ERROR: ref must be a non-empty string up to 1000 characters"
    if not isinstance(variables_json, str):
        return "GITLAB_ERROR: variables_json must be a string"
    try:
        variables = json.loads(variables_json)
    except json.JSONDecodeError:
        return "GITLAB_ERROR: variables_json must be valid JSON"
    if not isinstance(variables, list) or len(variables) > 100:
        return "GITLAB_ERROR: variables_json must decode to an array of at most 100 variables"
    normalized = []
    for variable in variables:
        if not isinstance(variable, dict) or set(variable) - {"key", "value", "variable_type"}:
            return "GITLAB_ERROR: each variable requires key/value and may include variable_type"
        key = variable.get("key")
        value = variable.get("value")
        variable_type = variable.get("variable_type", "env_var")
        if not isinstance(key, str) or not key or len(key) > 255 or not isinstance(value, str) or len(value) > 10000:
            return "GITLAB_ERROR: variable keys and values have invalid types or lengths"
        if variable_type not in {"env_var", "file"}:
            return "GITLAB_ERROR: variable_type must be env_var or file"
        normalized.append({"key": key, "value": value, "variable_type": variable_type})

    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"
    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/pipeline",
        data=json.dumps({"ref": ref.strip(), "variables": normalized}).encode(), method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while running the pipeline"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to run the pipeline ({type(error).__name__})"
    if not isinstance(payload, dict):
        return "GITLAB_ERROR: GitLab returned an invalid created pipeline"
    return json.dumps({key: payload.get(key) for key in ("id", "iid", "project_id", "sha", "ref", "status", "source", "created_at", "web_url")}, ensure_ascii=False)
