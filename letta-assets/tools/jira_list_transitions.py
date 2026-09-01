def jira_list_transitions(issue_key: str, include_fields: bool = True) -> str:
    """List workflow transitions currently available for one Jira issue.

    Args:
        issue_key: Jira issue key, for example ENG-123.
        include_fields: Include compact metadata for transition-screen fields.

    Returns:
        JSON containing transition IDs, names, target statuses, and required
        fields, or a JIRA_ERROR string.
    """
    import base64
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(issue_key, str) or not issue_key.strip():
        return "JIRA_ERROR: issue_key must be a non-empty string"
    if not isinstance(include_fields, bool):
        return "JIRA_ERROR: include_fields must be a boolean"

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

    query = urlencode({"expand": "transitions.fields"}) if include_fields else ""
    suffix = f"?{query}" if query else ""
    request = Request(
        f"{base_url}/rest/api/2/issue/{quote(issue_key.strip(), safe='')}/transitions{suffix}",
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
        return f"JIRA_ERROR: Jira returned HTTP {error.code} while listing transitions"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"JIRA_ERROR: unable to list transitions ({type(error).__name__})"

    raw_transitions = payload.get("transitions", []) if isinstance(payload, dict) else []
    if not isinstance(raw_transitions, list):
        return "JIRA_ERROR: Jira returned an invalid transition list"

    transitions = []
    for item in raw_transitions:
        if not isinstance(item, dict):
            continue
        target = item.get("to") if isinstance(item.get("to"), dict) else {}
        category = (
            target.get("statusCategory")
            if isinstance(target.get("statusCategory"), dict)
            else {}
        )
        compact_fields = {}
        raw_fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
        if include_fields:
            for field_id, metadata in raw_fields.items():
                if not isinstance(metadata, dict):
                    continue
                schema = metadata.get("schema") if isinstance(metadata.get("schema"), dict) else {}
                compact_fields[field_id] = {
                    "name": metadata.get("name"),
                    "required": metadata.get("required"),
                    "type": schema.get("type"),
                    "custom": schema.get("custom"),
                }
        transitions.append(
            {
                "id": item.get("id"),
                "name": item.get("name"),
                "target_status": target.get("name"),
                "target_status_category": category.get("name"),
                "has_screen": item.get("hasScreen"),
                "fields": compact_fields,
            }
        )

    return json.dumps(
        {
            "issue_key": issue_key.strip(),
            "transitions": transitions,
            "web_url": f"{base_url}/browse/{issue_key.strip()}",
        },
        ensure_ascii=False,
    )
