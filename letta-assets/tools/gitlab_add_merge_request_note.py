def gitlab_add_merge_request_note(
    project_id: str, merge_request_iid: int, body: str, internal: bool = False
) -> str:
    """Add one general comment to a GitLab merge request.

    This does not create a line-specific discussion. It is a write operation
    and requires the user's explicit authorization.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        merge_request_iid: Positive project-scoped merge-request IID.
        body: Markdown comment body.
        internal: Mark the note internal when the GitLab instance supports it.

    Returns:
        JSON containing the created note metadata, or a GITLAB_ERROR string.
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
    if not isinstance(body, str) or not body.strip() or len(body) > 1000000:
        return "GITLAB_ERROR: body must be a non-empty string up to 1000000 characters"
    if not isinstance(internal, bool):
        return "GITLAB_ERROR: internal must be a boolean"

    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"
    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/merge_requests/{merge_request_iid}/notes",
        data=json.dumps({"body": body, "internal": internal}).encode(), method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while adding the merge-request note"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to add the merge-request note ({type(error).__name__})"
    if not isinstance(payload, dict):
        return "GITLAB_ERROR: GitLab returned an invalid created note"
    author = payload.get("author") if isinstance(payload.get("author"), dict) else {}
    return json.dumps({"id": payload.get("id"), "author": author.get("username"), "created_at": payload.get("created_at"), "internal": payload.get("internal")}, ensure_ascii=False)
