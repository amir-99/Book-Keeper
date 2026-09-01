def gitlab_get_job_trace(project_id: str, job_id: int, max_trace_chars: int = 50000) -> str:
    """Read a bounded text trace for one GitLab CI job.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        job_id: Positive global job ID.
        max_trace_chars: Maximum trace characters, from 1000 through 100000.

    Returns:
        JSON containing the job trace and truncation flag, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(project_id, str) or not project_id.strip():
        return "GITLAB_ERROR: project_id must be a non-empty string"
    if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id < 1:
        return "GITLAB_ERROR: job_id must be a positive integer"
    if not isinstance(max_trace_chars, int) or isinstance(max_trace_chars, bool) or not 1000 <= max_trace_chars <= 100000:
        return "GITLAB_ERROR: max_trace_chars must be from 1000 through 100000"
    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"
    encoded_project = quote(project_id.strip(), safe="")
    request = Request(
        f"{base_url}/api/v4/projects/{encoded_project}/jobs/{job_id}/trace",
        headers={"Accept": "text/plain", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            trace = response.read(max_trace_chars + 1).decode("utf-8", errors="replace")
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while reading the job trace"
    except (URLError, OSError, ValueError) as error:
        return f"GITLAB_ERROR: unable to read the job trace ({type(error).__name__})"
    truncated = len(trace) > max_trace_chars
    if truncated:
        trace = trace[:max_trace_chars] + "\n[TRUNCATED]"
    return json.dumps({"job_id": job_id, "trace": trace, "truncated": truncated}, ensure_ascii=False)
