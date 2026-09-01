def jira_search_issues(jql: str, limit: int = 20, start_at: int = 0) -> str:
    """Search Jira issues with Jira Query Language (JQL).

    Use this for issue discovery. It returns compact metadata; call
    jira_get_issue with a result key when full issue details are needed.

    Args:
        jql: A JQL expression, for example ``project = ENG ORDER BY updated DESC``.
        limit: Number of results to return, from 1 through 50.
        start_at: Zero-based result offset.

    Returns:
        JSON containing matching issue summaries and pagination, or a
        JIRA_ERROR string.
    """
    import base64
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(jql, str) or not jql.strip():
        return "JIRA_ERROR: jql must be a non-empty string"
    if len(jql) > 4000:
        return "JIRA_ERROR: jql must not exceed 4000 characters"
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        return "JIRA_ERROR: limit must be an integer from 1 through 50"
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

    field_names = (
        "summary,status,issuetype,priority,project,assignee,reporter,created,updated,"
        "resolution,resolutiondate,labels,components,fixVersions,parent,duedate"
    )
    query = urlencode(
        {
            "jql": jql.strip(),
            "startAt": start_at,
            "maxResults": limit,
            "fields": field_names,
        }
    )
    request = Request(
        f"{base_url}/rest/api/2/search?{query}",
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
        return f"JIRA_ERROR: Jira returned HTTP {error.code} while searching issues"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"JIRA_ERROR: unable to search issues ({type(error).__name__})"

    raw_issues = payload.get("issues", []) if isinstance(payload, dict) else []
    if not isinstance(raw_issues, list):
        return "JIRA_ERROR: Jira returned an invalid issue search result"

    issues = []
    for issue in raw_issues:
        if not isinstance(issue, dict):
            continue
        fields = issue.get("fields") if isinstance(issue.get("fields"), dict) else {}
        status = fields.get("status") if isinstance(fields.get("status"), dict) else {}
        issue_type = (
            fields.get("issuetype") if isinstance(fields.get("issuetype"), dict) else {}
        )
        priority = (
            fields.get("priority") if isinstance(fields.get("priority"), dict) else {}
        )
        project = fields.get("project") if isinstance(fields.get("project"), dict) else {}
        assignee = (
            fields.get("assignee") if isinstance(fields.get("assignee"), dict) else {}
        )
        reporter = (
            fields.get("reporter") if isinstance(fields.get("reporter"), dict) else {}
        )
        resolution = (
            fields.get("resolution") if isinstance(fields.get("resolution"), dict) else {}
        )
        parent = fields.get("parent") if isinstance(fields.get("parent"), dict) else {}
        components = fields.get("components") if isinstance(fields.get("components"), list) else []
        versions = fields.get("fixVersions") if isinstance(fields.get("fixVersions"), list) else []
        key = issue.get("key")
        issues.append(
            {
                "id": issue.get("id"),
                "key": key,
                "summary": fields.get("summary"),
                "status": status.get("name"),
                "issue_type": issue_type.get("name"),
                "priority": priority.get("name"),
                "project_key": project.get("key"),
                "assignee": assignee.get("displayName") or assignee.get("name"),
                "reporter": reporter.get("displayName") or reporter.get("name"),
                "resolution": resolution.get("name"),
                "created_at": fields.get("created"),
                "updated_at": fields.get("updated"),
                "resolved_at": fields.get("resolutiondate"),
                "due_date": fields.get("duedate"),
                "labels": fields.get("labels") if isinstance(fields.get("labels"), list) else [],
                "components": [item.get("name") for item in components if isinstance(item, dict)],
                "fix_versions": [item.get("name") for item in versions if isinstance(item, dict)],
                "parent_key": parent.get("key"),
                "web_url": f"{base_url}/browse/{key}" if isinstance(key, str) and key else "",
            }
        )

    return json.dumps(
        {
            "issues": issues,
            "start_at": payload.get("startAt", start_at),
            "max_results": payload.get("maxResults", limit),
            "returned": len(issues),
            "total": payload.get("total"),
        },
        ensure_ascii=False,
    )
