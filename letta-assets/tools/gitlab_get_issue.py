def gitlab_get_issue(project_id: str, issue_iid: int, max_description_chars: int = 50000) -> str:
    """Read one GitLab issue by its project-scoped IID.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        issue_iid: Positive project-scoped issue IID, not the global ID.
        max_description_chars: Maximum description characters, from 1000 through 100000.

    Returns:
        JSON containing issue details, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    if not isinstance(issue_iid, int) or isinstance(issue_iid, bool) or issue_iid < 1:
        return "GITLAB_ERROR: issue_iid must be a positive integer"
    if not isinstance(max_description_chars, int) or isinstance(max_description_chars, bool) or not 1000 <= max_description_chars <= 100000:
        return "GITLAB_ERROR: max_description_chars must be from 1000 through 100000"

    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"
    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/issues/{issue_iid}",
        headers={"Accept": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while reading the issue"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to read the issue ({type(error).__name__})"
    if not isinstance(payload, dict):
        return "GITLAB_ERROR: GitLab returned an invalid issue"
    description = payload.get("description") if isinstance(payload.get("description"), str) else ""
    truncated = len(description) > max_description_chars
    if truncated:
        description = description[:max_description_chars] + "\n[TRUNCATED]"
    result = dict(payload)
    result["description"] = description
    result["description_truncated"] = truncated
    return json.dumps(result, ensure_ascii=False)
