def gitlab_delete_merge_request_draft_note(project_id: str, merge_request_iid: int, draft_note_id: int) -> str:
    """Discard one unpublished draft note from a merge request.

    Only an unpublished draft can be discarded this way. A published comment is
    never removed by this function.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        merge_request_iid: Positive project-scoped merge-request IID.
        draft_note_id: Positive identifier of the draft note to discard.

    Returns:
        JSON confirming the discarded draft, or a GITLAB_ERROR string.
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
    if not isinstance(draft_note_id, int) or isinstance(draft_note_id, bool) or draft_note_id < 1:
        return "GITLAB_ERROR: draft_note_id must be a positive integer"

    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"

    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/merge_requests/{merge_request_iid}/draft_notes/{draft_note_id}",
        method="DELETE",
        headers={"Accept": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30):
            pass
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while discarding the draft note"
    except (URLError, OSError, ValueError) as error:
        return f"GITLAB_ERROR: unable to discard the draft note ({type(error).__name__})"
    return json.dumps({"draft_note_id": draft_note_id, "merge_request_iid": merge_request_iid, "discarded": True}, ensure_ascii=False)
