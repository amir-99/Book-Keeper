def gitlab_create_merge_request(
    project_id: str, source_branch: str, target_branch: str, title: str,
    description: str = "", remove_source_branch: bool = False, squash: bool = False, draft: bool = False
) -> str:
    """Create one GitLab merge request.

    This is a write operation and requires the user's explicit authorization.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        source_branch: Existing source branch name.
        target_branch: Existing target branch name.
        title: Merge-request title.
        description: Optional Markdown description.
        remove_source_branch: Remove the source branch after merge when true.
        squash: Request commit squashing when true.
        draft: Create the merge request as a draft when true.

    Returns:
        JSON containing the created merge-request metadata, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    for name, value, maximum in (("source_branch", source_branch, 1000), ("target_branch", target_branch, 1000), ("title", title, 255)):
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            return f"GITLAB_ERROR: {name} must be a non-empty string up to {maximum} characters"
    if source_branch.strip() == target_branch.strip():
        return "GITLAB_ERROR: source_branch and target_branch must differ"
    if not isinstance(description, str) or len(description) > 1000000:
        return "GITLAB_ERROR: description must be a string up to 1000000 characters"
    if not all(isinstance(value, bool) for value in (remove_source_branch, squash, draft)):
        return "GITLAB_ERROR: remove_source_branch, squash, and draft must be booleans"

    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"
    effective_title = title.strip()
    if draft and not effective_title.lower().startswith(("draft:", "wip:")):
        effective_title = "Draft: " + effective_title
    body = {"source_branch": source_branch.strip(), "target_branch": target_branch.strip(), "title": effective_title, "description": description, "remove_source_branch": remove_source_branch, "squash": squash}
    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/merge_requests",
        data=json.dumps(body).encode(), method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while creating the merge request"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to create the merge request ({type(error).__name__})"
    if not isinstance(payload, dict):
        return "GITLAB_ERROR: GitLab returned an invalid created merge request"
    return json.dumps({key: payload.get(key) for key in ("id", "iid", "title", "state", "draft", "source_branch", "target_branch", "web_url", "created_at")}, ensure_ascii=False)
