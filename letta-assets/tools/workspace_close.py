def workspace_close(review_id: str, reason: str = "analysis_complete") -> str:
    """Close a review record and atomically discard any workspace it owns.

    Args:
        review_id: Review identifier returned by workspace_open.
        reason: Short lifecycle reason such as analysis_complete or stale.

    Returns:
        JSON confirming record closure and workspace discard, or a concise
        REVIEW_ERROR string.
    """
    import json
    import os
    import re
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    if not isinstance(review_id, str) or not re.fullmatch(r"rev_[0-9a-f]{32}", review_id):
        return "REVIEW_ERROR: review_id must be an identifier from workspace_open"
    if not isinstance(reason, str) or not reason.strip() or len(reason) > 80:
        return "REVIEW_ERROR: reason must be from 1 through 80 characters"
    base_url = os.getenv("REVIEW_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("REVIEW_API_KEY", "")
    if not base_url:
        return "REVIEW_ERROR: REVIEW_BASE_URL is unavailable"
    if not api_key:
        return "REVIEW_ERROR: REVIEW_API_KEY is unavailable"
    body = json.dumps({"reason": reason.strip()}).encode()
    request = Request(
        f"{base_url}/reviews/{quote(review_id, safe='')}/close",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "letta-review-tools/1.0",
        },
    )
    try:
        with urlopen(request, timeout=60) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        try:
            failure = json.loads(error.read(4096)).get("error")
        except (ValueError, json.JSONDecodeError, AttributeError):
            failure = None
        return f"REVIEW_ERROR: {failure}" if isinstance(failure, str) else f"REVIEW_ERROR: review service returned HTTP {error.code} while closing the review"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"REVIEW_ERROR: unable to close the review ({type(error).__name__})"
    if not isinstance(payload, dict) or payload.get("review_id") != review_id or payload.get("status") != "closed":
        return "REVIEW_ERROR: review service returned an unexpected close confirmation"
    return json.dumps(payload, ensure_ascii=False)

