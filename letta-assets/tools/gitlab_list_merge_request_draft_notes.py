def gitlab_list_merge_request_draft_notes(project_id: str, merge_request_iid: int, max_body_chars: int = 2000) -> str:
    """List the unpublished draft notes staged on one merge request.

    Draft notes are visible only to their author, so this returns the drafts
    belonging to the configured credential.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        merge_request_iid: Positive project-scoped merge-request IID.
        max_body_chars: Maximum body characters per draft, from 200 through 20000.

    Returns:
        JSON containing compact draft-note metadata, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    if not isinstance(merge_request_iid, int) or isinstance(merge_request_iid, bool) or merge_request_iid < 1:
        return "GITLAB_ERROR: merge_request_iid must be a positive integer"
    if not isinstance(max_body_chars, int) or isinstance(max_body_chars, bool) or not 200 <= max_body_chars <= 20000:
        return "GITLAB_ERROR: max_body_chars must be from 200 through 20000"

    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"

    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/merge_requests/{merge_request_iid}/draft_notes",
        headers={"Accept": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while listing draft notes"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to list draft notes ({type(error).__name__})"
    if not isinstance(payload, list):
        return "GITLAB_ERROR: GitLab returned an invalid draft-note list"

    drafts = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        position = item.get("position") if isinstance(item.get("position"), dict) else {}
        note = item.get("note") if isinstance(item.get("note"), str) else ""
        drafts.append(
            {
                "id": item.get("id"),
                "new_path": position.get("new_path"),
                "old_path": position.get("old_path"),
                "new_line": position.get("new_line"),
                "old_line": position.get("old_line"),
                "head_sha": position.get("head_sha"),
                "resolve_discussion": item.get("resolve_discussion"),
                "note": note[:max_body_chars] + ("\n[TRUNCATED]" if len(note) > max_body_chars else ""),
            }
        )
    return json.dumps({"merge_request_iid": merge_request_iid, "drafts": drafts, "returned": len(drafts)}, ensure_ascii=False)
