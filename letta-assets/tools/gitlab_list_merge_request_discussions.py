def gitlab_list_merge_request_discussions(
    project_id: str, merge_request_iid: int, limit: int = 50, page: int = 1, max_body_chars: int = 1000
) -> str:
    """List published discussion threads on one merge request.

    Use this before staging a review so lines that already carry a comment are
    not commented on twice, and so existing reviewer feedback is taken into
    account.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        merge_request_iid: Positive project-scoped merge-request IID.
        limit: Threads per page, from 1 through 100.
        page: One-based result page.
        max_body_chars: Maximum body characters per note, from 200 through 10000.

    Returns:
        JSON containing compact thread and note metadata, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    if not isinstance(merge_request_iid, int) or isinstance(merge_request_iid, bool) or merge_request_iid < 1:
        return "GITLAB_ERROR: merge_request_iid must be a positive integer"
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        return "GITLAB_ERROR: limit must be an integer from 1 through 100"
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        return "GITLAB_ERROR: page must be a positive integer"
    if not isinstance(max_body_chars, int) or isinstance(max_body_chars, bool) or not 200 <= max_body_chars <= 10000:
        return "GITLAB_ERROR: max_body_chars must be from 200 through 10000"

    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"

    encoded_project = quote(project_id.strip(), safe="")
    query = urlencode({"per_page": limit, "page": page})
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/merge_requests/{merge_request_iid}/discussions?{query}",
        headers={"Accept": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while listing merge-request discussions"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to list merge-request discussions ({type(error).__name__})"
    if not isinstance(payload, list):
        return "GITLAB_ERROR: GitLab returned an invalid discussion list"

    threads = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        notes = []
        for note in item.get("notes") if isinstance(item.get("notes"), list) else []:
            if not isinstance(note, dict):
                continue
            author = note.get("author") if isinstance(note.get("author"), dict) else {}
            position = note.get("position") if isinstance(note.get("position"), dict) else {}
            text = note.get("body") if isinstance(note.get("body"), str) else ""
            notes.append(
                {
                    "id": note.get("id"),
                    "author": author.get("username"),
                    "system": note.get("system"),
                    "resolved": note.get("resolved"),
                    "created_at": note.get("created_at"),
                    "new_path": position.get("new_path"),
                    "new_line": position.get("new_line"),
                    "old_line": position.get("old_line"),
                    "body": text[:max_body_chars] + ("\n[TRUNCATED]" if len(text) > max_body_chars else ""),
                }
            )
        threads.append({"id": item.get("id"), "individual_note": item.get("individual_note"), "notes": notes})
    return json.dumps(
        {"merge_request_iid": merge_request_iid, "discussions": threads, "page": page, "limit": limit, "returned": len(threads)},
        ensure_ascii=False,
    )
