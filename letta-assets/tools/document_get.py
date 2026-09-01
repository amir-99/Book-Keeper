def document_get(document_id: str, max_chars: int = 50000) -> str:
    """Read one stored document's Markdown source and metadata.

    Args:
        document_id: Identifier returned by document_create.
        max_chars: Maximum characters of Markdown to return, from 1000 through 1000000.

    Returns:
        JSON with title, revision, timestamps, and bounded Markdown, or a
        DOCUMENTS_ERROR string.
    """
    import json
    import os
    import re
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode
    from urllib.request import Request, urlopen

    if not isinstance(document_id, str) or not re.fullmatch(r"doc_[0-9a-f]{32}", document_id):
        return "DOCUMENTS_ERROR: document_id must be an identifier from document_create"
    if not isinstance(max_chars, int) or isinstance(max_chars, bool) or not 1000 <= max_chars <= 1000000:
        return "DOCUMENTS_ERROR: max_chars must be from 1000 through 1000000"

    base_url = os.getenv("DOCUMENTS_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("DOCUMENTS_API_KEY", "")
    if not base_url:
        return "DOCUMENTS_ERROR: DOCUMENTS_BASE_URL is unavailable"
    if not api_key:
        return "DOCUMENTS_ERROR: DOCUMENTS_API_KEY is unavailable"

    query = urlencode({"max_chars": max_chars})
    request = Request(
        f"{base_url}/documents/{quote(document_id, safe='')}?{query}",
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
        return f"DOCUMENTS_ERROR: document service returned HTTP {error.code} while reading the document"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"DOCUMENTS_ERROR: unable to read the document ({type(error).__name__})"
    if not isinstance(payload, dict) or not isinstance(payload.get("markdown"), str):
        return "DOCUMENTS_ERROR: document service returned an unexpected payload"
    return json.dumps(payload, ensure_ascii=False)
