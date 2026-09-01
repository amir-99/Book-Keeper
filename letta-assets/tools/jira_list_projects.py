def jira_list_projects(limit: int = 50, start_at: int = 0) -> str:
    """List Jira projects visible to the configured credential.

    Use this discovery tool before searching or creating issues when the
    destination project key is unknown.

    Args:
        limit: Maximum projects to return, from 1 through 100.
        start_at: Zero-based offset applied to the visible project list.

    Returns:
        JSON containing compact project metadata, or a JIRA_ERROR string.
    """
    import base64
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        return "JIRA_ERROR: limit must be an integer from 1 through 100"
    if not isinstance(start_at, int) or isinstance(start_at, bool) or start_at < 0:
        return "JIRA_ERROR: start_at must be a non-negative integer"

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

    request = Request(
        f"{base_url}/rest/api/2/project",
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
        return f"JIRA_ERROR: Jira returned HTTP {error.code} while listing projects"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"JIRA_ERROR: unable to list projects ({type(error).__name__})"

    if not isinstance(payload, list):
        return "JIRA_ERROR: Jira returned an invalid project list"
    selected = payload[start_at : start_at + limit]
    projects = []
    for item in selected:
        if not isinstance(item, dict):
            continue
        lead = item.get("lead") if isinstance(item.get("lead"), dict) else {}
        key = item.get("key")
        projects.append(
            {
                "id": item.get("id"),
                "key": key,
                "name": item.get("name"),
                "project_type_key": item.get("projectTypeKey"),
                "archived": item.get("archived"),
                "lead": lead.get("displayName") or lead.get("name"),
                "web_url": f"{base_url}/plugins/servlet/project-config/{key}/summary"
                if isinstance(key, str) and key
                else "",
            }
        )

    return json.dumps(
        {
            "projects": projects,
            "start_at": start_at,
            "limit": limit,
            "returned": len(projects),
            "total_visible": len(payload),
        },
        ensure_ascii=False,
    )
