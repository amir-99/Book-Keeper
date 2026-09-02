def review_progress_report(review_id: str, stage: str, detail: str) -> str:
    """Append one bounded, non-sensitive progress event to a review record.

    Args:
        review_id: Review identifier returned by workspace_open.
        stage: Lowercase stage name such as gitlab, context, workspace, or analyst.
        detail: One short factual progress line without credentials or raw payloads.

    Returns:
        JSON acknowledging the appended event and cursor, or a concise
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
    if not isinstance(stage, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", stage):
        return "REVIEW_ERROR: stage must be a short lowercase identifier"
    if not isinstance(detail, str) or not detail.strip() or len(detail.strip()) > 240 or "\n" in detail:
        return "REVIEW_ERROR: detail must be one line from 1 through 240 characters"
    base_url = os.getenv("REVIEW_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("REVIEW_API_KEY", "")
    if not base_url or not api_key:
        return "REVIEW_ERROR: review service configuration is unavailable"
    body = json.dumps({"stage": stage, "detail": detail.strip()}).encode()
    request = Request(
        f"{base_url}/reviews/{quote(review_id, safe='')}/events",
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
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        try:
            failure = json.loads(error.read(4096)).get("error")
        except (ValueError, json.JSONDecodeError, AttributeError):
            failure = None
        return f"REVIEW_ERROR: {failure}" if isinstance(failure, str) else f"REVIEW_ERROR: review service returned HTTP {error.code} while reporting progress"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"REVIEW_ERROR: unable to report review progress ({type(error).__name__})"
    if not isinstance(payload, dict) or not isinstance(payload.get("cursor"), int):
        return "REVIEW_ERROR: review service returned an unexpected progress acknowledgement"
    return json.dumps({"review_id": review_id, "cursor": payload["cursor"], "accepted": True})
