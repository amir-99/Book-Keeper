def gitlab_create_issue(
    project_id: str, title: str, description: str = "", labels: str = "",
    assignee_ids_json: str = "[]", confidential: bool = False, due_date: str = ""
) -> str:
    """Create one issue in a GitLab project.

    This is a write operation and requires the user's explicit authorization.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        title: New issue title.
        description: Optional Markdown description.
        labels: Optional comma-separated labels.
        assignee_ids_json: JSON array of numeric GitLab user IDs.
        confidential: Whether the issue is confidential.
        due_date: Optional due date in YYYY-MM-DD form.

    Returns:
        JSON containing the created issue identifiers and URL, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    if not isinstance(title, str) or not title.strip() or len(title) > 255:
        return "GITLAB_ERROR: title must be a non-empty string up to 255 characters"
    if not isinstance(description, str) or len(description) > 1000000:
        return "GITLAB_ERROR: description must be a string up to 1000000 characters"
    if not isinstance(labels, str) or len(labels) > 2000:
        return "GITLAB_ERROR: labels must be a string up to 2000 characters"
    if not isinstance(confidential, bool):
        return "GITLAB_ERROR: confidential must be a boolean"
    if not isinstance(due_date, str) or len(due_date) > 10:
        return "GITLAB_ERROR: due_date must be a string up to 10 characters"
    try:
        assignee_ids = json.loads(assignee_ids_json)
    except (TypeError, json.JSONDecodeError):
        return "GITLAB_ERROR: assignee_ids_json must be a JSON array"
    if not isinstance(assignee_ids, list) or any(not isinstance(value, int) or isinstance(value, bool) or value < 1 for value in assignee_ids):
        return "GITLAB_ERROR: assignee_ids_json must contain only positive integer user IDs"

    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"
    body = {"title": title.strip(), "description": description, "confidential": confidential, "assignee_ids": assignee_ids}
    if labels.strip():
        body["labels"] = labels.strip()
    if due_date.strip():
        body["due_date"] = due_date.strip()
    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/issues",
        data=json.dumps(body).encode(), method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while creating the issue"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to create the issue ({type(error).__name__})"
    if not isinstance(payload, dict):
        return "GITLAB_ERROR: GitLab returned an invalid created issue"
    return json.dumps({key: payload.get(key) for key in ("id", "iid", "title", "state", "web_url", "created_at")}, ensure_ascii=False)
