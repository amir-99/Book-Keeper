def jira_get_comments(
    issue_key: str, limit: int = 20, start_at: int = 0, max_body_chars: int = 10000
) -> str:
    """Read comments on one Jira issue without changing it.

    Args:
        issue_key: Jira issue key, for example ENG-123.
        limit: Number of comments to return, from 1 through 50.
        start_at: Zero-based result offset.
        max_body_chars: Per-comment body limit, from 500 through 50000.

    Returns:
        JSON containing comments and pagination, or a JIRA_ERROR string.
    """
    import base64
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(issue_key, str) or not issue_key.strip():
        return "JIRA_ERROR: issue_key must be a non-empty string"
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        return "JIRA_ERROR: limit must be an integer from 1 through 50"
    if not isinstance(start_at, int) or isinstance(start_at, bool) or start_at < 0:
        return "JIRA_ERROR: start_at must be a non-negative integer"
    if (
        not isinstance(max_body_chars, int)
        or isinstance(max_body_chars, bool)
        or not 500 <= max_body_chars <= 50000
    ):
        return "JIRA_ERROR: max_body_chars must be from 500 through 50000"

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

    query = urlencode({"startAt": start_at, "maxResults": limit, "orderBy": "created"})
    request = Request(
        f"{base_url}/rest/api/2/issue/{quote(issue_key.strip(), safe='')}/comment?{query}",
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
        return f"JIRA_ERROR: Jira returned HTTP {error.code} while reading comments"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"JIRA_ERROR: unable to read comments ({type(error).__name__})"

    if not isinstance(payload, dict):
        return "JIRA_ERROR: Jira returned an invalid comment response"
    raw_comments = payload.get("comments", [])
    if not isinstance(raw_comments, list):
        return "JIRA_ERROR: Jira returned an invalid comment list"

    comments = []
    for item in raw_comments:
        if not isinstance(item, dict):
            continue
        author = item.get("author") if isinstance(item.get("author"), dict) else {}
        update_author = (
            item.get("updateAuthor") if isinstance(item.get("updateAuthor"), dict) else {}
        )
        visibility = item.get("visibility") if isinstance(item.get("visibility"), dict) else {}
        body = item.get("body", "")
        if not isinstance(body, str):
            body = json.dumps(body, ensure_ascii=False)
        truncated = len(body) > max_body_chars
        if truncated:
            body = body[:max_body_chars]
        comments.append(
            {
                "id": item.get("id"),
                "author": author.get("displayName") or author.get("name"),
                "update_author": update_author.get("displayName") or update_author.get("name"),
                "created_at": item.get("created"),
                "updated_at": item.get("updated"),
                "body": body,
                "body_truncated": truncated,
                "visibility": visibility or None,
            }
        )

    return json.dumps(
        {
            "issue_key": issue_key.strip(),
            "comments": comments,
            "start_at": payload.get("startAt", start_at),
            "max_results": payload.get("maxResults", limit),
            "returned": len(comments),
            "total": payload.get("total"),
            "web_url": f"{base_url}/browse/{issue_key.strip()}",
        },
        ensure_ascii=False,
    )
