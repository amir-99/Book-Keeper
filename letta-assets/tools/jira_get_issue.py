def jira_get_issue(
    issue_key: str,
    fields: str = "summary,description,status,issuetype,priority,project,assignee,reporter,creator,created,updated,resolution,resolutiondate,labels,components,fixVersions,versions,parent,subtasks,issuelinks,duedate,environment",
    max_text_chars: int = 30000,
) -> str:
    """Read one Jira issue and selected fields.

    Args:
        issue_key: Jira issue key, for example ENG-123.
        fields: Comma-separated Jira field IDs or names. Use ``*all`` only when
            custom fields are required because it can return a large payload.
        max_text_chars: Per-field string limit, from 1000 through 100000.

    Returns:
        JSON containing issue metadata and selected fields, or a JIRA_ERROR
        string. Long strings include a truncation marker.
    """
    import base64
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(issue_key, str) or not issue_key.strip():
        return "JIRA_ERROR: issue_key must be a non-empty string"
    if not isinstance(fields, str) or not fields.strip() or len(fields) > 2000:
        return "JIRA_ERROR: fields must be a non-empty comma-separated string up to 2000 characters"
    if (
        not isinstance(max_text_chars, int)
        or isinstance(max_text_chars, bool)
        or not 1000 <= max_text_chars <= 100000
    ):
        return "JIRA_ERROR: max_text_chars must be from 1000 through 100000"

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

    query = urlencode({"fields": fields.strip()})
    request = Request(
        f"{base_url}/rest/api/2/issue/{quote(issue_key.strip(), safe='')}?{query}",
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": authorization,
            "User-Agent": "letta-jira-tools/1.0",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"JIRA_ERROR: Jira returned HTTP {error.code} while reading the issue"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"JIRA_ERROR: unable to read the issue ({type(error).__name__})"

    if not isinstance(payload, dict):
        return "JIRA_ERROR: Jira returned an invalid issue"

    selected_fields = payload.get("fields", {})
    if not isinstance(selected_fields, dict):
        return "JIRA_ERROR: Jira returned invalid issue fields"
    key = payload.get("key")
    if not isinstance(key, str) or not key:
        return "JIRA_ERROR: Jira returned an issue without a key"
    selected_fields = dict(selected_fields)
    truncated_fields = []
    for field_name, value in selected_fields.items():
        if isinstance(value, str) and len(value) > max_text_chars:
            selected_fields[field_name] = value[:max_text_chars] + "\n[TRUNCATED]"
            truncated_fields.append(field_name)
    return json.dumps(
        {
            "id": payload.get("id"),
            "key": key,
            "fields": selected_fields,
            "truncated_fields": sorted(truncated_fields),
            "web_url": f"{base_url}/browse/{key}" if isinstance(key, str) and key else "",
        },
        ensure_ascii=False,
    )
