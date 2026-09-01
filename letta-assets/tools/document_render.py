def document_render(document_id: str, output_format: str = "docx") -> str:
    """Render a stored document and return a download link for the user.

    Relay the returned download_url exactly as received. A shortened, relabelled,
    or reconstructed URL is a 404.

    Args:
        document_id: Identifier returned by document_create.
        output_format: One of docx, pdf, html, odt, txt, or md.

    Returns:
        JSON with filename, format, byte size, and download_url, or a
        DOCUMENTS_ERROR string.
    """
    import json
    import os
    import re
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    allowed = {"docx", "pdf", "html", "odt", "txt", "md"}
    if not isinstance(document_id, str) or not re.fullmatch(r"doc_[0-9a-f]{32}", document_id):
        return "DOCUMENTS_ERROR: document_id must be an identifier from document_create"
    if output_format not in allowed:
        return f"DOCUMENTS_ERROR: output_format must be one of {sorted(allowed)}"

    base_url = os.getenv("DOCUMENTS_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("DOCUMENTS_API_KEY", "")
    if not base_url:
        return "DOCUMENTS_ERROR: DOCUMENTS_BASE_URL is unavailable"
    if not api_key:
        return "DOCUMENTS_ERROR: DOCUMENTS_API_KEY is unavailable"

    body = json.dumps({"format": output_format}).encode()
    request = Request(
        f"{base_url}/documents/{quote(document_id, safe='')}/render",
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
        with urlopen(request, timeout=180) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"DOCUMENTS_ERROR: document service returned HTTP {error.code} while rendering"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"DOCUMENTS_ERROR: unable to render the document ({type(error).__name__})"
    if not isinstance(payload, dict) or not isinstance(payload.get("download_url"), str):
        return "DOCUMENTS_ERROR: document service returned an unexpected payload"
    return json.dumps(payload, ensure_ascii=False)
