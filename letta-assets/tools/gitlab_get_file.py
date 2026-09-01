def gitlab_get_file(
    project_id: str, file_path: str, ref: str = "HEAD", max_content_chars: int = 50000
) -> str:
    """Read one UTF-8 text file from a GitLab repository.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        file_path: Full path to the file inside the repository.
        ref: Branch, tag, or commit SHA; HEAD selects the default branch.
        max_content_chars: Maximum decoded characters to return, from 1000 through 100000.

    Returns:
        JSON containing file metadata and bounded text content, or a
        GITLAB_ERROR string. Binary files return metadata without content.
    """
    import base64
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    if not isinstance(file_path, str) or not file_path.strip() or len(file_path) > 4000:
        return "GITLAB_ERROR: file_path must be a non-empty string up to 4000 characters"
    if not isinstance(ref, str) or not ref.strip() or len(ref) > 1000:
        return "GITLAB_ERROR: ref must be a non-empty string up to 1000 characters"
    if not isinstance(max_content_chars, int) or isinstance(max_content_chars, bool) or not 1000 <= max_content_chars <= 100000:
        return "GITLAB_ERROR: max_content_chars must be from 1000 through 100000"

    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"
    encoded_project = quote(project_id.strip(), safe="")
    encoded_path = quote(file_path.strip("/"), safe="")
    query = urlencode({"ref": ref.strip()})
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/repository/files/{encoded_path}?{query}",
        headers={"Accept": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while reading the file"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to read the file ({type(error).__name__})"
    if not isinstance(payload, dict) or payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
        return "GITLAB_ERROR: GitLab returned an unsupported file payload"
    try:
        decoded = base64.b64decode(payload["content"], validate=False)
        content = decoded.decode("utf-8")
        is_binary = False
    except (ValueError, UnicodeDecodeError):
        content = ""
        is_binary = True
    truncated = len(content) > max_content_chars
    if truncated:
        content = content[:max_content_chars] + "\n[TRUNCATED]"
    result = {key: payload.get(key) for key in ("file_name", "file_path", "size", "ref", "blob_id", "commit_id", "last_commit_id", "content_sha256", "execute_filemode")}
    result.update({"content": content, "binary": is_binary, "truncated": truncated})
    return json.dumps(result, ensure_ascii=False)
