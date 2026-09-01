def document_delete(document_id: str) -> str:
    """Delete one stored document and every render made from it.

    This is a destructive write operation. Invoke it only when the user
    explicitly asks for that document to be deleted. Already-delivered download
    links are not revoked by this call.

    Args:
        document_id: Identifier returned by document_create.

    Returns:
        JSON confirming the deletion, or a DOCUMENTS_ERROR string.
    """
    import json
    import os
    import re
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    if not isinstance(document_id, str) or not re.fullmatch(r"doc_[0-9a-f]{32}", document_id):
        return "DOCUMENTS_ERROR: document_id must be an identifier from document_create"

    base_url = os.getenv("DOCUMENTS_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("DOCUMENTS_API_KEY", "")
    if not base_url:
        return "DOCUMENTS_ERROR: DOCUMENTS_BASE_URL is unavailable"
    if not api_key:
        return "DOCUMENTS_ERROR: DOCUMENTS_API_KEY is unavailable"

    request = Request(
        f"{base_url}/documents/{quote(document_id, safe='')}",
        method="DELETE",
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
        return f"DOCUMENTS_ERROR: document service returned HTTP {error.code} while deleting the document"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"DOCUMENTS_ERROR: unable to delete the document ({type(error).__name__})"
    if not isinstance(payload, dict) or payload.get("deleted") is not True:
        return "DOCUMENTS_ERROR: document service did not confirm the deletion"
    return json.dumps(payload, ensure_ascii=False)
