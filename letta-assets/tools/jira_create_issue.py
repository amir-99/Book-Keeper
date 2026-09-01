def jira_create_issue(
    project_key: str,
    issue_type: str,
    summary: str,
    description: str = "",
    extra_fields: dict = None,
) -> str:
    """Create one Jira issue.

    This is a write operation. Invoke it only when the user explicitly asks
    for an issue to be created and the project, type, and summary are clear.

    Args:
        project_key: Destination Jira project key.
        issue_type: Jira issue type name, such as Task or Bug.
        summary: New issue summary.
        description: Optional plain-text Jira description.
        extra_fields: Optional object of additional Jira fields, such as
            parent, labels, components, priority, or custom fields.

    Returns:
        JSON identifying the created issue, or a JIRA_ERROR string.
    """
    import base64
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(project_key, str) or not project_key.strip():
        return "JIRA_ERROR: project_key must be a non-empty string"
    if not isinstance(issue_type, str) or not issue_type.strip():
        return "JIRA_ERROR: issue_type must be a non-empty string"
    if not isinstance(summary, str) or not summary.strip():
        return "JIRA_ERROR: summary must be a non-empty string"
    if not isinstance(description, str):
        return "JIRA_ERROR: description must be a string"
    if extra_fields is None:
        extra_fields = {}
    if not isinstance(extra_fields, dict):
        return "JIRA_ERROR: extra_fields must be an object"

    base_url = os.getenv("JIRA_BASE_URL", "").strip().rstrip("/")
    credential = os.getenv("JIRA_ACCESS_TOKEN", "")
    auth_mode = os.getenv("JIRA_AUTH_MODE", "bearer").strip().lower()
    parsed_base_url = urlsplit(base_url)
    if parsed_base_url.scheme != "https" or not parsed_base_url.netloc:
        return "JIRA_ERROR: JIRA_BASE_URL must be an HTTPS URL"
    if not credential:
        return "JIRA_ERROR: JIRA_ACCESS_TOKEN is unavailable"
    if auth_mode == "basic":
        authorization = "Basic " + base64.b64encode(credential.encode()).decode()
    elif auth_mode == "basic_encoded":
        authorization = "Basic " + credential
    elif auth_mode == "bearer":
        authorization = "Bearer " + credential
    else:
        return "JIRA_ERROR: JIRA_AUTH_MODE must be bearer, basic, or basic_encoded"

    fields = dict(extra_fields)
    fields.update(
        {
            "project": {"key": project_key.strip()},
            "issuetype": {"name": issue_type.strip()},
            "summary": summary.strip(),
        }
    )
    if description:
        fields["description"] = description

    request = Request(
        f"{base_url}/rest/api/2/issue",
        data=json.dumps({"fields": fields}).encode(),
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": authorization,
            "Content-Type": "application/json",
            "User-Agent": "letta-jira-tools/1.0",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"JIRA_ERROR: Jira returned HTTP {error.code} while creating the issue"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"JIRA_ERROR: unable to create the issue ({type(error).__name__})"

    if not isinstance(payload, dict):
        return "JIRA_ERROR: Jira returned an invalid created issue"
    key = payload.get("key")
    if not isinstance(key, str) or not key:
        return "JIRA_ERROR: Jira did not return a key for the created issue"
    return json.dumps(
        {
            "id": payload.get("id"),
            "key": key,
            "web_url": f"{base_url}/browse/{key}" if isinstance(key, str) and key else "",
        },
        ensure_ascii=False,
    )
