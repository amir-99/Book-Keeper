def workspace_open(
    project_id: str, merge_request_iid: int, target_branch: str,
    expected_head_sha: str, base_sha: str, create_workspace: bool = True,
) -> str:
    """Open a review record and optionally fetch its pinned repository workspace.

    The record is always created so every review has an event stream. Set
    create_workspace to false only when the manager selected the quick tier and
    GitLab reported no unavailable patch content.

    Args:
        project_id: Full GitLab project path such as group/project.
        merge_request_iid: Positive project-scoped merge-request IID.
        target_branch: Target branch reported by the reviewed merge request.
        expected_head_sha: Full head SHA returned by the GitLab evidence read.
        base_sha: Full base SHA from the same merge request diff_refs.
        create_workspace: Whether to fetch a disposable repository workspace.

    Returns:
        JSON with review_id and optional workspace metadata, a REVIEW_STALE
        line, or a concise REVIEW_ERROR string.
    """
    import json
    import os
    import re
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    if not isinstance(project_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+", project_id):
        return "REVIEW_ERROR: project_id must be a full GitLab project path"
    if not isinstance(merge_request_iid, int) or isinstance(merge_request_iid, bool) or merge_request_iid < 1:
        return "REVIEW_ERROR: merge_request_iid must be a positive integer"
    if not isinstance(target_branch, str) or not target_branch or len(target_branch) > 255:
        return "REVIEW_ERROR: target_branch must be a non-empty branch name"
    if not isinstance(expected_head_sha, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", expected_head_sha):
        return "REVIEW_ERROR: expected_head_sha must be a full commit SHA"
    if not isinstance(base_sha, str) or not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", base_sha):
        return "REVIEW_ERROR: base_sha must be a full commit SHA"
    if not isinstance(create_workspace, bool):
        return "REVIEW_ERROR: create_workspace must be a boolean"

    base_url = os.getenv("REVIEW_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("REVIEW_API_KEY", "")
    if not base_url:
        return "REVIEW_ERROR: REVIEW_BASE_URL is unavailable"
    if not api_key:
        return "REVIEW_ERROR: REVIEW_API_KEY is unavailable"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "letta-review-tools/1.0",
    }
    review_body = json.dumps({
        "project": project_id,
        "merge_request_iid": merge_request_iid,
        "expected_head_sha": expected_head_sha,
    }).encode()
    try:
        request = Request(f"{base_url}/reviews", data=review_body, method="POST", headers=headers)
        with urlopen(request, timeout=30) as response:
            review = json.loads(response.read())
    except HTTPError as error:
        try:
            failure = json.loads(error.read(4096)).get("error")
        except (ValueError, json.JSONDecodeError, AttributeError):
            failure = None
        return f"REVIEW_ERROR: {failure}" if isinstance(failure, str) else f"REVIEW_ERROR: review service returned HTTP {error.code} while opening the review"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"REVIEW_ERROR: unable to open the review ({type(error).__name__})"
    review_id = review.get("review_id") if isinstance(review, dict) else None
    if not isinstance(review_id, str) or not re.fullmatch(r"rev_[0-9a-f]{32}", review_id):
        return "REVIEW_ERROR: review service returned an unexpected review record"
    if not create_workspace:
        return json.dumps({
            "review_id": review_id,
            "workspace_id": None,
            "head_sha": expected_head_sha,
            "base_sha": base_sha,
            "workspace_opened": False,
        }, ensure_ascii=False)

    workspace_body = json.dumps({
        "review_id": review_id,
        "project": project_id,
        "merge_request_iid": merge_request_iid,
        "target_branch": target_branch,
        "expected_head_sha": expected_head_sha,
        "base_sha": base_sha,
    }).encode()
    try:
        request = Request(f"{base_url}/workspaces", data=workspace_body, method="POST", headers=headers)
        with urlopen(request, timeout=180) as response:
            workspace = json.loads(response.read())
    except HTTPError as error:
        try:
            failure = json.loads(error.read(4096)).get("error")
        except (ValueError, json.JSONDecodeError, AttributeError):
            failure = None
        try:
            close_body = json.dumps({"reason": "workspace_open_failed"}).encode()
            close_request = Request(
                f"{base_url}/reviews/{quote(review_id, safe='')}/close",
                data=close_body, method="POST", headers=headers,
            )
            with urlopen(close_request, timeout=30):
                pass
        except (HTTPError, URLError, OSError, ValueError):
            pass
        if isinstance(failure, str) and failure.startswith("REVIEW_STALE:"):
            return failure
        return f"REVIEW_ERROR: {failure}" if isinstance(failure, str) else f"REVIEW_ERROR: review service returned HTTP {error.code} while fetching the workspace"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"REVIEW_ERROR: unable to fetch the workspace ({type(error).__name__})"
    workspace_id = workspace.get("workspace_id") if isinstance(workspace, dict) else None
    if not isinstance(workspace_id, str) or not re.fullmatch(r"ws_[0-9a-f]{32}", workspace_id):
        return "REVIEW_ERROR: review service returned an unexpected workspace"
    try:
        detail = f"fetched {workspace.get('head_sha', '')[:8]} at depth {workspace.get('fetch_depth', 0)}"
        event_body = json.dumps({"stage": "workspace", "detail": detail}).encode()
        event_request = Request(
            f"{base_url}/reviews/{quote(review_id, safe='')}/events",
            data=event_body, method="POST", headers=headers,
        )
        with urlopen(event_request, timeout=30):
            pass
    except (HTTPError, URLError, OSError, ValueError):
        pass
    result = dict(workspace)
    result["review_id"] = review_id
    result["workspace_opened"] = True
    return json.dumps(result, ensure_ascii=False)

