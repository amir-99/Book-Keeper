def gitlab_list_repository_tree(
    project_id: str, path: str = "", ref: str = "HEAD", recursive: bool = False,
    limit: int = 100, page: int = 1
) -> str:
    """List files and directories in a GitLab repository.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        path: Optional directory path within the repository.
        ref: Branch, tag, or commit SHA; HEAD selects the default branch.
        recursive: Recursively list descendants when true.
        limit: Results per page, from 1 through 100.
        page: One-based result page.

    Returns:
        JSON containing repository tree entries, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    if not isinstance(path, str) or len(path) > 4000:
        return "GITLAB_ERROR: path must be a string up to 4000 characters"
    if not isinstance(ref, str) or not ref.strip() or len(ref) > 1000:
        return "GITLAB_ERROR: ref must be a non-empty string up to 1000 characters"
    if not isinstance(recursive, bool):
        return "GITLAB_ERROR: recursive must be a boolean"
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        return "GITLAB_ERROR: limit must be an integer from 1 through 100"
    if not isinstance(page, int) or isinstance(page, bool) or page < 1:
        return "GITLAB_ERROR: page must be a positive integer"

    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"
    params = {"path": path.strip("/"), "ref": ref.strip(), "recursive": str(recursive).lower(), "per_page": limit, "page": page}
    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/repository/tree?{urlencode(params)}",
        headers={"Accept": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while listing the repository tree"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to list the repository tree ({type(error).__name__})"
    if not isinstance(payload, list):
        return "GITLAB_ERROR: GitLab returned an invalid repository tree"
    entries = [{key: item.get(key) for key in ("id", "name", "type", "path", "mode")} for item in payload if isinstance(item, dict)]
    return json.dumps({"entries": entries, "path": path.strip("/"), "ref": ref.strip(), "page": page, "limit": limit, "returned": len(entries)}, ensure_ascii=False)
