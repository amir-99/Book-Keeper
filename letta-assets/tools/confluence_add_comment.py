def confluence_add_comment(page_id: str, body_storage: str) -> str:
    """Add a comment to a Confluence page using storage-format XHTML.

    Args:
        page_id: ID of the page that will receive the comment.
        body_storage: Complete comment body in Confluence storage XHTML.

    Returns:
        JSON identifying the created comment and its parent page, or a
        CONFLUENCE_ERROR string.
    """
    import base64
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(page_id, str) or not page_id.strip():
        return "CONFLUENCE_ERROR: page_id must be a non-empty string"
    if not isinstance(body_storage, str) or not body_storage.strip():
        return "CONFLUENCE_ERROR: body_storage must be a non-empty string"

    base_url = os.getenv("CONFLUENCE_BASE_URL", "").strip().rstrip("/")
    credential = os.getenv("CONFLUENCE_ACCESS_TOKEN", "")
    auth_mode = os.getenv("CONFLUENCE_AUTH_MODE", "auto").strip().lower()
    parsed_base_url = urlsplit(base_url)
    if parsed_base_url.scheme != "https" or not parsed_base_url.netloc:
        return "CONFLUENCE_ERROR: CONFLUENCE_BASE_URL must be an HTTPS URL"
    if not credential:
        return "CONFLUENCE_ERROR: CONFLUENCE_ACCESS_TOKEN is unavailable"
    if auth_mode == "auto":
        auth_mode = "basic" if ":" in credential else "bearer"
    if auth_mode == "basic":
        authorization = "Basic " + base64.b64encode(credential.encode()).decode()
    elif auth_mode == "bearer":
        authorization = "Bearer " + credential
    else:
        return "CONFLUENCE_ERROR: CONFLUENCE_AUTH_MODE must be auto, basic, or bearer"

    request_payload = {
        "type": "comment",
        "container": {"id": page_id.strip(), "type": "page"},
        "body": {
            "storage": {
                "value": body_storage,
                "representation": "storage",
            }
        },
    }
    request = Request(
        f"{base_url}/rest/api/content",
        data=json.dumps(request_payload).encode(),
        method="POST",
        headers={
            "Accept": "application/json",
            "Authorization": authorization,
            "Content-Type": "application/json",
            "User-Agent": "letta-confluence-tools/1.0",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"CONFLUENCE_ERROR: Confluence returned HTTP {error.code} while adding the comment"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"CONFLUENCE_ERROR: unable to add the comment ({type(error).__name__})"

    if not isinstance(payload, dict):
        return "CONFLUENCE_ERROR: Confluence returned an invalid created comment"
    version = payload.get("version") if isinstance(payload.get("version"), dict) else {}
    links = payload.get("_links") if isinstance(payload.get("_links"), dict) else {}
    web_path = links.get("webui") if isinstance(links.get("webui"), str) else ""
    if web_path.startswith(("http://", "https://")):
        web_url = web_path
    elif web_path:
        web_url = f"{base_url}/{web_path.lstrip('/')}"
    else:
        web_url = ""

    return json.dumps(
        {
            "id": payload.get("id"),
            "type": payload.get("type"),
            "status": payload.get("status"),
            "page_id": page_id.strip(),
            "version": version.get("number"),
            "web_url": web_url,
        },
        ensure_ascii=False,
    )
