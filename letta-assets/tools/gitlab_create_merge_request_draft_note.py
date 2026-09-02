def gitlab_create_merge_request_draft_note(
    project_id: str, merge_request_iid: int, body: str,
    base_sha: str, head_sha: str, start_sha: str,
    new_path: str, old_path: str, new_line: int = 0, old_line: int = 0
) -> str:
    """Stage one unpublished, line-anchored draft note on a merge request.

    A draft note is visible only to its author until it is published, so this
    write is reversible and shows nothing to the merge request's participants.
    Publishing is a separate, explicitly authorized step.

    Anchor exactly as GitLab requires: an added line passes only `new_line`, a
    removed line passes only `old_line`, and an unchanged context line passes
    both. Pass 0 for the line number that does not apply.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        merge_request_iid: Positive project-scoped merge-request IID.
        body: Markdown comment body.
        base_sha: `diff_refs.base_sha` of the reviewed merge-request version.
        head_sha: `diff_refs.head_sha` of the reviewed merge-request version.
        start_sha: `diff_refs.start_sha` of the reviewed merge-request version.
        new_path: File path after the change.
        old_path: File path before the change; equal to new_path when unrenamed.
        new_line: Line number in the new file, or 0 when not applicable.
        old_line: Line number in the old file, or 0 when not applicable.

    Returns:
        JSON containing the created draft note metadata, or a GITLAB_ERROR string.
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
    for label, value in (("base_sha", base_sha), ("head_sha", head_sha), ("start_sha", start_sha)):
        if not isinstance(value, str) or not value.strip():
            return f"GITLAB_ERROR: {label} must be a non-empty string from the merge request's diff_refs"
    for label, value in (("new_path", new_path), ("old_path", old_path)):
        if not isinstance(value, str) or not value.strip():
            return f"GITLAB_ERROR: {label} must be a non-empty string"
    for label, value in (("new_line", new_line), ("old_line", old_line)):
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            return f"GITLAB_ERROR: {label} must be a non-negative integer"
    if not new_line and not old_line:
        return "GITLAB_ERROR: at least one of new_line or old_line must be a positive line number"

    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"

    position = {
        "position_type": "text",
        "base_sha": base_sha.strip(),
        "head_sha": head_sha.strip(),
        "start_sha": start_sha.strip(),
        "new_path": new_path.strip(),
        "old_path": old_path.strip(),
    }
    if new_line:
        position["new_line"] = new_line
    if old_line:
        position["old_line"] = old_line

    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/merge_requests/{merge_request_iid}/draft_notes",
        data=json.dumps({"note": body, "position": position}).encode(), method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while staging the draft note"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to stage the draft note ({type(error).__name__})"
    if not isinstance(payload, dict):
        return "GITLAB_ERROR: GitLab returned an invalid created draft note"
    return json.dumps(
        {
            "id": payload.get("id"),
            "merge_request_iid": merge_request_iid,
            "new_path": new_path.strip(),
            "new_line": new_line or None,
            "old_line": old_line or None,
            "published": False,
        },
        ensure_ascii=False,
    )
