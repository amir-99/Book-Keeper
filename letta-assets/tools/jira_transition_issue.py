def jira_transition_issue(
    issue_key: str, transition_id: str, fields_json: str = ""
) -> str:
    """Move one Jira issue through an explicitly selected workflow transition.

    Call jira_list_transitions first and pass an available transition ID. This
    is a write operation and requires the user's explicit authorization.

    Args:
        issue_key: Jira issue key, for example ENG-123.
        transition_id: Exact transition ID returned by jira_list_transitions.
        fields_json: Optional JSON object for required transition-screen fields.

    Returns:
        JSON confirming the requested transition, or a JIRA_ERROR string.
    """
    import base64
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(issue_key, str) or not issue_key.strip():
        return "JIRA_ERROR: issue_key must be a non-empty string"
    if not isinstance(transition_id, str) or not transition_id.strip():
        return "JIRA_ERROR: transition_id must be a non-empty string"
    if not isinstance(fields_json, str):
        return "JIRA_ERROR: fields_json must be a string"
    try:
        fields = json.loads(fields_json) if fields_json.strip() else {}
    except json.JSONDecodeError:
        return "JIRA_ERROR: fields_json must be a valid JSON object"
    if not isinstance(fields, dict):
        return "JIRA_ERROR: fields_json must decode to a JSON object"
    if set(fields) & {"comment", "comments"}:
        return "JIRA_ERROR: use jira_add_comment for comments"

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

    request_payload = {"transition": {"id": transition_id.strip()}}
    if fields:
        request_payload["fields"] = fields
    request = Request(
        f"{base_url}/rest/api/2/issue/{quote(issue_key.strip(), safe='')}/transitions",
        data=json.dumps(request_payload).encode(),
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
            response.read()
    except HTTPError as error:
        return f"JIRA_ERROR: Jira returned HTTP {error.code} while transitioning the issue"
    except (URLError, OSError, ValueError) as error:
        return f"JIRA_ERROR: unable to transition the issue ({type(error).__name__})"

    return json.dumps(
        {
            "issue_key": issue_key.strip(),
            "transition_id": transition_id.strip(),
            "submitted_fields": sorted(fields),
            "web_url": f"{base_url}/browse/{issue_key.strip()}",
        },
        ensure_ascii=False,
    )
