def confluence_update_page(
    page_id: str,
    body_storage: str,
    title: str = "",
    version_message: str = "",
    minor_edit: bool = False,
) -> str:
    """Replace a Confluence page body and optionally its title.

    The tool reads the latest version immediately before updating and submits
    the next version number. Confluence rejects concurrent edits rather than
    silently overwriting them.

    Args:
        page_id: ID of the page to update.
        body_storage: Complete replacement body in Confluence storage XHTML.
        title: Optional replacement title; omit to preserve the current title.
        version_message: Optional human-readable change summary.
        minor_edit: Whether Confluence should mark this as a minor edit.

    Returns:
        JSON identifying the updated page and version, or a CONFLUENCE_ERROR string.
    """
    import base64
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(page_id, str) or not page_id.strip():
        return "CONFLUENCE_ERROR: page_id must be a non-empty string"
    if not isinstance(body_storage, str) or not body_storage.strip():
        return "CONFLUENCE_ERROR: body_storage must be a non-empty string"
    if not isinstance(title, str):
        return "CONFLUENCE_ERROR: title must be a string"
    if not isinstance(version_message, str):
        return "CONFLUENCE_ERROR: version_message must be a string"
    if not isinstance(minor_edit, bool):
        return "CONFLUENCE_ERROR: minor_edit must be a boolean"

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

    headers = {
        "Accept": "application/json",
        "Authorization": authorization,
        "Content-Type": "application/json",
        "User-Agent": "letta-confluence-tools/1.0",
    }
    encoded_page_id = quote(page_id.strip(), safe="")
    version_query = urlencode({"expand": "version"})
    read_request = Request(
        f"{base_url}/rest/api/content/{encoded_page_id}?{version_query}",
        method="GET",
        headers=headers,
    )
    try:
        with urlopen(read_request, timeout=30) as response:
            current = json.loads(response.read())
    except HTTPError as error:
        return f"CONFLUENCE_ERROR: Confluence returned HTTP {error.code} while reading the current page version"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"CONFLUENCE_ERROR: unable to read the current page version ({type(error).__name__})"

    if not isinstance(current, dict):
        return "CONFLUENCE_ERROR: Confluence returned an invalid current page"
    current_version = current.get("version")
    current_version_number = (
        current_version.get("number") if isinstance(current_version, dict) else None
    )
    current_title = current.get("title")
    if not isinstance(current_version_number, int):
        return "CONFLUENCE_ERROR: current page has no numeric version"
    if not title.strip() and (not isinstance(current_title, str) or not current_title):
        return "CONFLUENCE_ERROR: current page has no title"

    request_payload = {
        "id": page_id.strip(),
        "type": "page",
        "title": title.strip() or current_title,
        "body": {
            "storage": {
                "value": body_storage,
                "representation": "storage",
            }
        },
        "version": {
            "number": current_version_number + 1,
            "minorEdit": minor_edit,
        },
    }
    if version_message.strip():
        request_payload["version"]["message"] = version_message.strip()

    update_request = Request(
        f"{base_url}/rest/api/content/{encoded_page_id}",
        data=json.dumps(request_payload).encode(),
        method="PUT",
        headers=headers,
    )
    try:
        with urlopen(update_request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        if error.code == 409:
            return "CONFLUENCE_ERROR: page changed concurrently; read it again before retrying"
        return f"CONFLUENCE_ERROR: Confluence returned HTTP {error.code} while updating the page"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"CONFLUENCE_ERROR: unable to update the page ({type(error).__name__})"

    if not isinstance(payload, dict):
        return "CONFLUENCE_ERROR: Confluence returned an invalid updated page"
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
            "previous_version": current_version_number,
            "version": version.get("number"),
            "web_url": web_url,
        },
        ensure_ascii=False,
    )
