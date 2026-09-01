def document_list(limit: int = 25, offset: int = 0) -> str:
    """List stored documents, most recently updated first.

    Args:
        limit: Maximum documents to return, from 1 through 100.
        offset: Number of documents to skip.

    Returns:
        JSON with a total count and compact document records, or a
        DOCUMENTS_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode
    from urllib.request import Request, urlopen

    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        return "DOCUMENTS_ERROR: limit must be from 1 through 100"
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        return "DOCUMENTS_ERROR: offset must be zero or greater"

    base_url = os.getenv("DOCUMENTS_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("DOCUMENTS_API_KEY", "")
    if not base_url:
        return "DOCUMENTS_ERROR: DOCUMENTS_BASE_URL is unavailable"
    if not api_key:
        return "DOCUMENTS_ERROR: DOCUMENTS_API_KEY is unavailable"

    query = urlencode({"limit": limit, "offset": offset})
    request = Request(
        f"{base_url}/documents?{query}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "letta-document-tools/1.0",
        },
    )
    try:
        with urlopen(request, timeout=60) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"DOCUMENTS_ERROR: document service returned HTTP {error.code} while listing documents"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"DOCUMENTS_ERROR: unable to list documents ({type(error).__name__})"
    if not isinstance(payload, dict) or not isinstance(payload.get("documents"), list):
        return "DOCUMENTS_ERROR: document service returned an unexpected payload"
    return json.dumps(payload, ensure_ascii=False)
