def gitlab_get_merge_request_review_diffs(
    project_id: str, merge_request_iid: int, limit: int = 50, page: int = 1,
    max_lines_per_file: int = 400, max_total_lines: int = 4000,
    include_lines: bool = True, paths: str = ""
) -> str:
    """Read one merge request's diff refs and per-line numbered diff for review.

    Returns the `diff_refs` needed to anchor a diff comment and the patches with
    every line's resolved `old_line` and `new_line`, so a caller never has to
    count lines from an `@@` hunk header. Both come from the same read, so the
    refs always describe the returned patches.

    The `coverage` object reports what this one response actually contains:
    files and lines returned, lines dropped by the caps, whether another page
    exists, and a `complete` flag that is true only when this response holds
    every changed line of the merge request. A caller aggregating several pages
    judges completeness across them, not from a single response.

    Set `include_lines` to false to receive the metadata and that same coverage
    accounting without the line arrays. The patches are still parsed and counted,
    so every number is exact; only the text is withheld. A caller that needs to
    know what changed but not what the lines say should always use it: the line
    arrays are by far the largest part of this response.

    Set `paths` to review chosen files rather than whole pages. It walks the
    merge request's pages itself and returns only the files named, so a caller
    that has already listed the change can pull the lines it needs without
    paging past everything else.

    GitLab renders patch text only for the earliest files of a merge request, up
    to a budget for the whole merge request, and returns every file past it with
    an empty diff and no flag saying so. Neither paging nor a smaller page size
    recovers one. Any file arriving without a patch for no stated reason is
    therefore reported as `content_unavailable`, never as a file that changed
    nothing, and a caller needing one reads it with `gitlab_get_file` at the
    reviewed `head_sha`.

    Args:
        project_id: Numeric project ID or full path such as group/project.
        merge_request_iid: Positive project-scoped merge-request IID.
        limit: Diff files per page, from 1 through 100.
        page: One-based result page.
        max_lines_per_file: Diff lines kept per file, from 20 through 2000.
        max_total_lines: Diff lines kept across all files, from 100 through 20000.
        include_lines: Return the numbered diff lines; false returns counts only.
        paths: Comma-separated file paths; only those files are returned.

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
    if not isinstance(include_lines, bool):
        return "GITLAB_ERROR: include_lines must be a boolean"
    if not isinstance(paths, str) or len(paths) > 20000:
        return "GITLAB_ERROR: paths must be a string up to 20000 characters"
    requested = [candidate.strip() for candidate in paths.split(",") if candidate.strip()]
    if len(requested) > 100:
        return "GITLAB_ERROR: paths must name at most 100 files"

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
    endpoint = f"{base_url}/api/v4/projects/{encoded_project}/merge_requests/{merge_request_iid}"
    try:
        with urlopen(Request(endpoint, headers=headers), timeout=30) as response:
            merge_request = json.loads(response.read())
    except HTTPError as error:
        return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while reading the merge request for review"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"GITLAB_ERROR: unable to read the merge request for review ({type(error).__name__})"

    # Naming paths selects files rather than a page, so the pages are walked here
    # and filtered; a caller that already listed the change should not have to
    # page past every file to reach the ones it means to review.
    payload = []
    next_page_header = None
    exhausted = True
    current_page = page
    for attempt in range(20 if requested else 1):
        query = urlencode({"per_page": limit, "page": current_page})
        try:
            with urlopen(Request(f"{endpoint}/diffs?{query}", headers=headers), timeout=30) as response:
                chunk = json.loads(response.read())
                if attempt == 0:
                    next_page_header = response.headers.get("X-Next-Page")
        except HTTPError as error:
            return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while reading the merge request for review"
        except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
            return f"GITLAB_ERROR: unable to read the merge request for review ({type(error).__name__})"
        if not isinstance(chunk, list):
            return "GITLAB_ERROR: GitLab returned invalid merge-request diffs"
        payload.extend(chunk)
        if not requested or len(chunk) < limit:
            break
        current_page += 1
    else:
        exhausted = False
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
    total_dropped = 0
    matched = set()
    for item in payload:
        if not isinstance(item, dict):
            continue
        if requested:
            names = {item.get("new_path"), item.get("old_path")} - {None}
            hit = names & set(requested)
            if not hit:
                continue
            matched |= hit
        entry = {key: item.get(key) for key in ("old_path", "new_path", "new_file", "renamed_file", "deleted_file", "generated_file", "too_large")}
        patch = item.get("diff") if isinstance(item.get("diff"), str) else ""
        # A rename with no edits and a mode-only change legitimately carry no
        # patch. Anything else arriving empty was withheld by the render budget,
        # and must not be mistaken for a file that changed nothing.
        mode_change = (
            not item.get("new_file")
            and not item.get("deleted_file")
            and item.get("a_mode") != item.get("b_mode")
        )
        content_unavailable = not patch and not item.get("renamed_file") and not mode_change
        lines = []
        old_no = new_no = 0
        dropped = 0
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
            marker, text = (raw[:1], raw[1:]) if raw else (" ", "")
            if marker not in ("+", "-", " "):
                continue
            # Past a cap the remaining lines are counted rather than dropped
            # silently, so the caller learns exactly how much evidence is missing
            # instead of inferring it from a boolean.
            if len(lines) >= max_lines_per_file or total_lines >= max_total_lines:
                dropped += 1
                continue
            if marker == "+":
                lines.append(["add", None, new_no, text])
                new_no += 1
            elif marker == "-":
                lines.append(["del", old_no, None, text])
                old_no += 1
            else:
                lines.append(["ctx", old_no, new_no, text])
                old_no += 1
                new_no += 1
            total_lines += 1
        total_dropped += dropped
        entry.update({
            "lines_returned": len(lines),
            "lines_dropped": dropped,
            "truncated": bool(dropped) or bool(item.get("too_large")),
            "content_unavailable": content_unavailable,
        })
        # The line arrays dominate this payload. Withholding them keeps a caller
        # that only needs coverage from pulling the whole diff into its context.
        if include_lines:
            entry["lines"] = lines
        files.append(entry)

    # changes_count is the merge request's own file count and may arrive as an
    # approximate string such as "20+" on very large merge requests.
    changes_count = merge_request.get("changes_count")
    files_total = None
    files_total_approximate = False
    if isinstance(changes_count, int) and not isinstance(changes_count, bool):
        files_total = changes_count
    elif isinstance(changes_count, str):
        counted = changes_count.strip()
        files_total_approximate = counted.endswith("+")
        digits = counted[:-1] if files_total_approximate else counted
        if digits.isdigit():
            files_total = int(digits)

    if requested:
        # Every page was walked, so the only unread remainder is a merge request
        # larger than the page-walk cap.
        next_page = current_page + 1 if not exhausted else None
        has_more_pages = not exhausted
    elif next_page_header is None:
        # Older GitLab releases omit the pagination header on this endpoint; a
        # full page is then the only evidence that another one may exist.
        next_page = None
        has_more_pages = len(payload) >= limit
    else:
        stripped = next_page_header.strip()
        next_page = int(stripped) if stripped.isdigit() else None
        has_more_pages = next_page is not None
    files_truncated = [entry.get("new_path") or entry.get("old_path") for entry in files if entry["truncated"]]
    files_unavailable = [entry.get("new_path") or entry.get("old_path") for entry in files if entry["content_unavailable"]]
    paths_not_found = [name for name in requested if name not in matched]
    complete = (
        not files_truncated
        and not files_unavailable
        and total_dropped == 0
        and not has_more_pages
        and (
            not paths_not_found
            if requested
            else files_total is None or (not files_total_approximate and len(files) >= files_total)
        )
    )

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
            "lines_included": include_lines,
            "files": files,
            "page": page,
            "limit": limit,
            "returned": len(files),
            "total_lines": total_lines,
            "coverage": {
                "files_total": files_total,
                "files_total_approximate": files_total_approximate,
                "files_returned": len(files),
                "files_truncated": files_truncated,
                "files_unavailable": files_unavailable,
                "lines_returned": total_lines,
                "lines_dropped": total_dropped,
                "paths_requested": requested,
                "paths_not_found": paths_not_found,
                "has_more_pages": has_more_pages,
                "next_page": next_page,
                "complete": complete,
            },
        },
        ensure_ascii=False,
    )
