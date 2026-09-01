def jira_add_comment(issue_key: str, body: str) -> str:
    """Add one plain-text comment to a Jira issue.

    This is a write operation. Invoke it only when the user explicitly asks
    for the exact comment to be posted to the identified issue.

    Args:
        issue_key: Jira issue key, for example ENG-123.
        body: Complete plain-text comment body.

    Returns:
        JSON identifying the created comment, or a JIRA_ERROR string.
    """
    import base64
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(issue_key, str) or not issue_key.strip():
        return "JIRA_ERROR: issue_key must be a non-empty string"
    if not isinstance(body, str) or not body.strip():
        return "JIRA_ERROR: body must be a non-empty string"

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
        f"{base_url}/rest/api/2/issue/{quote(issue_key.strip(), safe='')}/comment",
        data=json.dumps({"body": body}).encode(),
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
        return f"JIRA_ERROR: Jira returned HTTP {error.code} while adding the comment"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"JIRA_ERROR: unable to add the comment ({type(error).__name__})"

    if not isinstance(payload, dict):
        return "JIRA_ERROR: Jira returned an invalid created comment"
    author = payload.get("author") if isinstance(payload.get("author"), dict) else {}
    return json.dumps(
        {
            "id": payload.get("id"),
            "issue_key": issue_key.strip(),
            "author": author.get("displayName") or author.get("name"),
            "created_at": payload.get("created"),
            "updated_at": payload.get("updated"),
            "web_url": f"{base_url}/browse/{issue_key.strip()}",
        },
        ensure_ascii=False,
    )
