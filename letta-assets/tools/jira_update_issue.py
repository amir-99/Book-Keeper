def jira_update_issue(issue_key: str, fields: dict, notify_users: bool = True) -> str:
    """Update fields on one existing Jira issue.

    Comments and workflow transitions have dedicated tools and must not be
    smuggled through this field-update operation.

    Args:
        issue_key: Jira issue key, for example ENG-123.
        fields: Non-empty object mapping Jira field IDs or names to their
            complete replacement values.
        notify_users: Whether Jira should send update notifications.

    Returns:
        JSON confirming the issue key and changed field names, or a JIRA_ERROR
        string.
    """
    import base64
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(issue_key, str) or not issue_key.strip():
        return "JIRA_ERROR: issue_key must be a non-empty string"
    if not isinstance(fields, dict) or not fields:
        return "JIRA_ERROR: fields must be a non-empty object"
    if not isinstance(notify_users, bool):
        return "JIRA_ERROR: notify_users must be a boolean"
    forbidden = sorted(set(fields) & {"comment", "comments", "status"})
    if forbidden:
        return "JIRA_ERROR: use the dedicated comment or transition tool instead of updating: " + ", ".join(forbidden)

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

    query = urlencode({"notifyUsers": "true" if notify_users else "false"})
    request = Request(
        f"{base_url}/rest/api/2/issue/{quote(issue_key.strip(), safe='')}?{query}",
        data=json.dumps({"fields": fields}).encode(),
        method="PUT",
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
        return f"JIRA_ERROR: Jira returned HTTP {error.code} while updating the issue"
    except (URLError, OSError, ValueError) as error:
        return f"JIRA_ERROR: unable to update the issue ({type(error).__name__})"

    return json.dumps(
        {
            "issue_key": issue_key.strip(),
            "updated_fields": sorted(fields),
            "notifications_requested": notify_users,
            "web_url": f"{base_url}/browse/{issue_key.strip()}",
        },
        ensure_ascii=False,
    )
