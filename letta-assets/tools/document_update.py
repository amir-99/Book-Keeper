def document_update(
    document_id: str,
    markdown: str = "",
    replace_old: str = "",
    replace_new: str = "",
    title: str = "",
) -> str:
    """Replace a document body, patch one unique span, or retitle a document.

    This is a write operation. Supply either markdown for a full replacement or
    replace_old with replace_new for a targeted edit, never both. replace_old
    must appear exactly once in the stored source.

    Args:
        document_id: Identifier returned by document_create.
        markdown: Complete replacement body in Markdown.
        replace_old: Exact existing span to replace, unique in the document.
        replace_new: Replacement text for replace_old; may be empty to delete the span.
        title: Optional new document title.

    Returns:
        JSON with the new revision and metadata, or a DOCUMENTS_ERROR string.
    """
    import json
    import os
    import re
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote
    from urllib.request import Request, urlopen

    if not isinstance(document_id, str) or not re.fullmatch(r"doc_[0-9a-f]{32}", document_id):
        return "DOCUMENTS_ERROR: document_id must be an identifier from document_create"
    for name, value in (("markdown", markdown), ("replace_old", replace_old), ("replace_new", replace_new), ("title", title)):
        if not isinstance(value, str):
            return f"DOCUMENTS_ERROR: {name} must be a string"
    if markdown and replace_old:
        return "DOCUMENTS_ERROR: supply either markdown or replace_old, not both"
    if replace_new and not replace_old:
        return "DOCUMENTS_ERROR: replace_new requires replace_old"
    if not markdown and not replace_old and not title.strip():
        return "DOCUMENTS_ERROR: supply markdown, replace_old, or title"
    if len(markdown.encode()) > 1000000:
        return "DOCUMENTS_ERROR: markdown must be at most 1000000 bytes"
    if len(title) > 200:
        return "DOCUMENTS_ERROR: title must be at most 200 characters"

    base_url = os.getenv("DOCUMENTS_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("DOCUMENTS_API_KEY", "")
    if not base_url:
        return "DOCUMENTS_ERROR: DOCUMENTS_BASE_URL is unavailable"
    if not api_key:
        return "DOCUMENTS_ERROR: DOCUMENTS_API_KEY is unavailable"

    changes = {}
    if markdown:
        changes["markdown"] = markdown
    elif replace_old:
        changes["str_replace"] = {"old": replace_old, "new": replace_new}
    if title.strip():
        changes["title"] = title.strip()

    request = Request(
        f"{base_url}/documents/{quote(document_id, safe='')}",
        data=json.dumps(changes).encode(),
        method="PATCH",
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
        return f"DOCUMENTS_ERROR: document service returned HTTP {error.code} while updating the document"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"DOCUMENTS_ERROR: unable to update the document ({type(error).__name__})"
    if not isinstance(payload, dict) or not isinstance(payload.get("revision"), int):
        return "DOCUMENTS_ERROR: document service returned an unexpected payload"
    return json.dumps(payload, ensure_ascii=False)
