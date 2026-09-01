def confluence_get_page(
    page_id: str, body_format: str = "storage", max_body_chars: int = 20000
) -> str:
    """Read one Confluence page and its body.

    Args:
        page_id: Numeric or string content ID returned by Confluence search.
        body_format: Body representation: storage, view, or export_view.
        max_body_chars: Maximum body characters returned, from 1000 to 100000.

    Returns:
        JSON containing page metadata, ancestors, and body content, or a
        CONFLUENCE_ERROR string. body_truncated reports whether output was cut.
    """
    import base64
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(page_id, str) or not page_id.strip():
        return "CONFLUENCE_ERROR: page_id must be a non-empty string"
    if body_format not in {"storage", "view", "export_view"}:
        return "CONFLUENCE_ERROR: body_format must be storage, view, or export_view"
    if (
        not isinstance(max_body_chars, int)
        or isinstance(max_body_chars, bool)
        or not 1000 <= max_body_chars <= 100000
    ):
        return "CONFLUENCE_ERROR: max_body_chars must be from 1000 through 100000"

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

    query = urlencode({"expand": f"space,version,ancestors,body.{body_format}"})
    request = Request(
        f"{base_url}/rest/api/content/{quote(page_id.strip(), safe='')}?{query}",
        method="GET",
        headers={
            "Accept": "application/json",
            "Authorization": authorization,
            "User-Agent": "letta-confluence-tools/1.0",
        },
    )
    try:
        with urlopen(request, timeout=30) as response:
            payload = json.loads(response.read())
    except HTTPError as error:
        return f"CONFLUENCE_ERROR: Confluence returned HTTP {error.code} while reading the page"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"CONFLUENCE_ERROR: unable to read the page ({type(error).__name__})"

    if not isinstance(payload, dict):
        return "CONFLUENCE_ERROR: Confluence returned an invalid page"
    space = payload.get("space") if isinstance(payload.get("space"), dict) else {}
    version = payload.get("version") if isinstance(payload.get("version"), dict) else {}
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    representation = body.get(body_format) if isinstance(body.get(body_format), dict) else {}
    body_value = representation.get("value", "")
    if not isinstance(body_value, str):
        body_value = ""
    body_truncated = len(body_value) > max_body_chars
    if body_truncated:
        body_value = body_value[:max_body_chars]

    ancestors = []
    raw_ancestors = payload.get("ancestors", [])
    if isinstance(raw_ancestors, list):
        for ancestor in raw_ancestors:
            if isinstance(ancestor, dict):
                ancestors.append(
                    {"id": ancestor.get("id"), "title": ancestor.get("title")}
                )

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
            "type": payload.get("type"),
            "status": payload.get("status"),
            "space_key": space.get("key"),
            "space_name": space.get("name"),
            "version": version.get("number"),
            "updated_at": version.get("when"),
            "ancestors": ancestors,
            "body_format": body_format,
            "body": body_value,
            "body_truncated": body_truncated,
            "web_url": web_url,
        },
        ensure_ascii=False,
    )
