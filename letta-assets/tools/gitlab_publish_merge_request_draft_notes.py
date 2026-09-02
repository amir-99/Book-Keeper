def gitlab_publish_merge_request_draft_notes(
    project_id: str, merge_request_iid: int, summary_note: str = "", reviewer_state: str = ""
) -> str:
    """Publish every staged draft note on one merge request as a single review.

    This is the only step that makes the review visible to the merge request's
    participants, and it cannot be undone by discarding drafts afterwards. It
    requires the user's explicit authorization. It never approves or merges.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        merge_request_iid: Positive project-scoped merge-request IID.
        summary_note: Optional Markdown summary published alongside the drafts.
        reviewer_state: Optional reviewer state: reviewed or requested_changes.

    Returns:
        JSON confirming publication, or a GITLAB_ERROR string.
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
    if not isinstance(summary_note, str) or len(summary_note) > 1000000:
        return "GITLAB_ERROR: summary_note must be a string up to 1000000 characters"
    if not isinstance(reviewer_state, str) or reviewer_state not in {"", "reviewed", "requested_changes"}:
        return "GITLAB_ERROR: reviewer_state must be empty, reviewed, or requested_changes"

    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"

    fields = {}
    if summary_note.strip():
        fields["note"] = summary_note
    if reviewer_state:
        fields["reviewer_state"] = reviewer_state

    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/merge_requests/{merge_request_iid}/draft_notes/bulk_publish",
        data=json.dumps(fields).encode(), method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=60) as response:
            status = response.status
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while publishing the staged review"
    except (URLError, OSError, ValueError) as error:
        return f"GITLAB_ERROR: unable to publish the staged review ({type(error).__name__})"
    return json.dumps(
        {
            "merge_request_iid": merge_request_iid,
            "published": True,
            "status": status,
            "reviewer_state": reviewer_state or None,
            "summary_published": bool(summary_note.strip()),
        },
        ensure_ascii=False,
    )
