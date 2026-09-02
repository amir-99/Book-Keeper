def gitlab_get_merge_request_review_diffs(
    project_id: str, merge_request_iid: int, limit: int = 50, page: int = 1,
    max_lines_per_file: int = 400, max_total_lines: int = 4000
) -> str:
    """Read one merge request's diff refs and per-line numbered diff for review.

    Returns the `diff_refs` needed to anchor a diff comment and the patches with
    every line's resolved `old_line` and `new_line`, so a caller never has to
    count lines from an `@@` hunk header. Both come from the same read, so the
    refs always describe the returned patches.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        merge_request_iid: Positive project-scoped merge-request IID.
        limit: Diff files per page, from 1 through 100.
        page: One-based result page.
        max_lines_per_file: Diff lines kept per file, from 20 through 2000.
        max_total_lines: Diff lines kept across all files, from 100 through 20000.

    Returns:
        JSON containing merge-request metadata, diff refs, and numbered diff
        lines, or a GITLAB_ERROR string.
    """
    import json
    import os
    import re
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
    if not isinstance(max_lines_per_file, int) or isinstance(max_lines_per_file, bool) or not 20 <= max_lines_per_file <= 2000:
        return "GITLAB_ERROR: max_lines_per_file must be from 20 through 2000"
    if not isinstance(max_total_lines, int) or isinstance(max_total_lines, bool) or not 100 <= max_total_lines <= 20000:
        return "GITLAB_ERROR: max_total_lines must be from 100 through 20000"

    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"
    encoded_project = quote(project_id.strip(), safe="")
    headers = {"Accept": "application/json", "PRIVATE-TOKEN": token, "User-Agent": "letta-gitlab-tools/1.0"}

    # The merge request carries diff_refs and the diffs endpoint carries the
    # patches. Reading both here keeps the refs and the patches consistent.
    query = urlencode({"per_page": limit, "page": page})
    results = []
    for path in (f"merge_requests/{merge_request_iid}", f"merge_requests/{merge_request_iid}/diffs?{query}"):
        try:
            with urlopen(Request(f"{base_url}/api/v4/projects/{encoded_project}/{path}", headers=headers), timeout=30) as response:
                results.append(json.loads(response.read()))
        except HTTPError as error:
            return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while reading the merge request for review"
        except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
            return f"GITLAB_ERROR: unable to read the merge request for review ({type(error).__name__})"

    merge_request, payload = results
    if not isinstance(merge_request, dict):
        return "GITLAB_ERROR: GitLab returned an invalid merge request"
    diff_refs = merge_request.get("diff_refs") if isinstance(merge_request.get("diff_refs"), dict) else {}
    if not all(isinstance(diff_refs.get(key), str) and diff_refs.get(key) for key in ("base_sha", "head_sha", "start_sha")):
        return "GITLAB_ERROR: merge request has no usable diff_refs; it cannot be reviewed with line comments"
    if not isinstance(payload, list):
        return "GITLAB_ERROR: GitLab returned invalid merge-request diffs"

    hunk_header = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
    files = []
    total_lines = 0
    for item in payload:
        if not isinstance(item, dict):
            continue
        entry = {key: item.get(key) for key in ("old_path", "new_path", "new_file", "renamed_file", "deleted_file", "generated_file", "too_large")}
        patch = item.get("diff") if isinstance(item.get("diff"), str) else ""
        lines = []
        old_no = new_no = 0
        truncated = False
        # A patch normally ends with a newline; splitting it directly would add a
        # phantom trailing context line and desynchronize every later line number.
        for raw in (patch[:-1] if patch.endswith("\n") else patch).split("\n"):
            if raw.startswith("\\"):
                continue
            match = hunk_header.match(raw)
            if match:
                old_no, new_no = int(match.group(1)), int(match.group(3))
                continue
            if old_no == 0 and new_no == 0:
                continue
            if len(lines) >= max_lines_per_file or total_lines >= max_total_lines:
                truncated = True
                break
            marker, text = (raw[:1], raw[1:]) if raw else (" ", "")
            if marker == "+":
                lines.append(["add", None, new_no, text])
                new_no += 1
            elif marker == "-":
                lines.append(["del", old_no, None, text])
                old_no += 1
            elif marker == " ":
                lines.append(["ctx", old_no, new_no, text])
                old_no += 1
                new_no += 1
            else:
                continue
            total_lines += 1
        entry.update({"lines": lines, "truncated": truncated or bool(item.get("too_large"))})
        files.append(entry)

    return json.dumps(
        {
            "project_id": merge_request.get("project_id"),
            "merge_request_iid": merge_request_iid,
            "title": merge_request.get("title"),
            "state": merge_request.get("state"),
            "draft": merge_request.get("draft"),
            "source_branch": merge_request.get("source_branch"),
            "target_branch": merge_request.get("target_branch"),
            "web_url": merge_request.get("web_url"),
            "diff_refs": {key: diff_refs[key] for key in ("base_sha", "head_sha", "start_sha")},
            "line_format": ["type", "old_line", "new_line", "text"],
            "files": files,
            "page": page,
            "limit": limit,
            "returned": len(files),
            "total_lines": total_lines,
        },
        ensure_ascii=False,
    )
