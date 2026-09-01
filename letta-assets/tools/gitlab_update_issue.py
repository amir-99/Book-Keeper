def gitlab_update_issue(project_id: str, issue_iid: int, fields_json: str) -> str:
    """Update explicitly supplied fields on one GitLab issue.

    Comments have a dedicated tool and cannot be submitted here. This is a
    write operation and requires the user's explicit authorization.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        issue_iid: Positive project-scoped issue IID.
        fields_json: Non-empty JSON object of supported GitLab issue fields.

    Returns:
        JSON containing the updated issue metadata, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlsplit
    from urllib.request import Request, urlopen

    allowed_fields = {"title", "description", "confidential", "assignee_ids", "milestone_id", "labels", "add_labels", "remove_labels", "due_date", "state_event", "discussion_locked", "issue_type", "weight"}
    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    if not isinstance(issue_iid, int) or isinstance(issue_iid, bool) or issue_iid < 1:
        return "GITLAB_ERROR: issue_iid must be a positive integer"
    if not isinstance(fields_json, str) or not fields_json.strip():
        return "GITLAB_ERROR: fields_json must be a non-empty JSON object"
    try:
        fields = json.loads(fields_json)
    except json.JSONDecodeError:
        return "GITLAB_ERROR: fields_json must be valid JSON"
    if not isinstance(fields, dict) or not fields:
        return "GITLAB_ERROR: fields_json must decode to a non-empty object"
    unsupported = sorted(set(fields) - allowed_fields)
    if unsupported:
        return "GITLAB_ERROR: unsupported issue fields: " + ", ".join(unsupported)

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
        data=json.dumps(fields).encode(), method="PUT",
        headers={"Accept": "application/json", "Content-Type": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while updating the issue"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to update the issue ({type(error).__name__})"
    if not isinstance(payload, dict):
        return "GITLAB_ERROR: GitLab returned an invalid updated issue"
    result = {key: payload.get(key) for key in ("id", "iid", "title", "state", "web_url", "updated_at")}
    result["updated_fields"] = sorted(fields)
    return json.dumps(result, ensure_ascii=False)
