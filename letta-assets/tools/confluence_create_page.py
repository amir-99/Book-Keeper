def confluence_create_page(
    space_key: str, title: str, body_storage: str, parent_id: str = ""
) -> str:
    """Create a Confluence page using storage-format XHTML.

    Args:
        space_key: Key of the destination Confluence space.
        title: New page title.
        body_storage: Complete page body in Confluence storage XHTML.
        parent_id: Optional parent page ID; omit to create at the space root.

    Returns:
        JSON identifying the created page and URL, or a CONFLUENCE_ERROR string.
    """
    import base64
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(space_key, str) or not space_key.strip():
        return "CONFLUENCE_ERROR: space_key must be a non-empty string"
    if not isinstance(title, str) or not title.strip():
        return "CONFLUENCE_ERROR: title must be a non-empty string"
    if not isinstance(body_storage, str) or not body_storage.strip():
        return "CONFLUENCE_ERROR: body_storage must be a non-empty string"
    if not isinstance(parent_id, str):
        return "CONFLUENCE_ERROR: parent_id must be a string"

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
        "type": "page",
        "title": title.strip(),
        "space": {"key": space_key.strip()},
        "body": {
            "storage": {
                "value": body_storage,
                "representation": "storage",
            }
        },
    }
    if parent_id.strip():
        request_payload["ancestors"] = [{"id": parent_id.strip()}]

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
        return f"CONFLUENCE_ERROR: Confluence returned HTTP {error.code} while creating the page"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"CONFLUENCE_ERROR: unable to create the page ({type(error).__name__})"

    if not isinstance(payload, dict):
        return "CONFLUENCE_ERROR: Confluence returned an invalid created page"
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
            "title": payload.get("title"),
            "status": payload.get("status"),
            "space_key": space_key.strip(),
            "parent_id": parent_id.strip() or None,
            "version": version.get("number"),
            "web_url": web_url,
        },
        ensure_ascii=False,
    )
