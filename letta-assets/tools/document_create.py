def document_create(title: str, markdown: str) -> str:
    """Create a document from Markdown and return its identifier.

    Markdown is the stored source of truth. Rendered .docx, .pdf, .html, .odt,
    and .txt files are produced later by document_render.

    Args:
        title: Human-readable document title, up to 200 characters.
        markdown: Full document body in Markdown.

    Returns:
        JSON with document_id, title, revision, and timestamps, or a
        DOCUMENTS_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    if not isinstance(title, str) or not title.strip() or len(title) > 200:
        return "DOCUMENTS_ERROR: title must be a non-empty string up to 200 characters"
    if not isinstance(markdown, str) or not markdown.strip():
        return "DOCUMENTS_ERROR: markdown must be a non-empty string"
    if len(markdown.encode()) > 1000000:
        return "DOCUMENTS_ERROR: markdown must be at most 1000000 bytes"

    base_url = os.getenv("DOCUMENTS_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("DOCUMENTS_API_KEY", "")
    if not base_url:
        return "DOCUMENTS_ERROR: DOCUMENTS_BASE_URL is unavailable"
    if not api_key:
        return "DOCUMENTS_ERROR: DOCUMENTS_API_KEY is unavailable"

    body = json.dumps({"title": title.strip(), "markdown": markdown}).encode()
    request = Request(
        f"{base_url}/documents",
        data=body,
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "letta-document-tools/1.0",
        },
    )
    try:
        with urlopen(request, timeout=60) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"DOCUMENTS_ERROR: document service returned HTTP {error.code} while creating the document"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"DOCUMENTS_ERROR: unable to create the document ({type(error).__name__})"
    if not isinstance(payload, dict) or not isinstance(payload.get("document_id"), str):
        return "DOCUMENTS_ERROR: document service returned an unexpected payload"
    return json.dumps(payload, ensure_ascii=False)
