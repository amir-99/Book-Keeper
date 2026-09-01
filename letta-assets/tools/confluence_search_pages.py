def confluence_search_pages(cql: str, limit: int = 10, start: int = 0) -> str:
    """Search Confluence pages with Confluence Query Language (CQL).

    Use this for discovery only. It returns compact page metadata; call
    confluence_get_page with a result ID when the page body is needed.

    Args:
        cql: A CQL expression, for example ``space = ENG AND text ~ "deploy"``.
        limit: Number of results to return, from 1 through 50.
        start: Zero-based result offset.

    Returns:
        JSON containing matching page IDs, titles, spaces, versions, and URLs,
        or a CONFLUENCE_ERROR string.
    """
    import base64
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(cql, str) or not cql.strip():
        return "CONFLUENCE_ERROR: cql must be a non-empty string"
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        return "CONFLUENCE_ERROR: limit must be an integer from 1 through 50"
    if not isinstance(start, int) or isinstance(start, bool) or start < 0:
        return "CONFLUENCE_ERROR: start must be a non-negative integer"

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

    query = urlencode(
        {
            "cql": cql.strip(),
            "expand": "space,version",
            "limit": limit,
            "start": start,
        }
    )
    request = Request(
        f"{base_url}/rest/api/content/search?{query}",
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
        return f"CONFLUENCE_ERROR: Confluence returned HTTP {error.code} while searching pages"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"CONFLUENCE_ERROR: unable to search pages ({type(error).__name__})"

    raw_results = payload.get("results", []) if isinstance(payload, dict) else []
    if not isinstance(raw_results, list):
        return "CONFLUENCE_ERROR: Confluence returned an invalid search result"

    results = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        space = item.get("space") if isinstance(item.get("space"), dict) else {}
        version = item.get("version") if isinstance(item.get("version"), dict) else {}
        links = item.get("_links") if isinstance(item.get("_links"), dict) else {}
        web_path = links.get("webui") if isinstance(links.get("webui"), str) else ""
        if web_path.startswith(("http://", "https://")):
            web_url = web_path
        elif web_path:
            web_url = f"{base_url}/{web_path.lstrip('/')}"
        else:
            web_url = ""
        results.append(
            {
                "id": item.get("id"),
                "title": item.get("title"),
                "type": item.get("type"),
                "status": item.get("status"),
                "space_key": space.get("key"),
                "space_name": space.get("name"),
                "version": version.get("number"),
                "updated_at": version.get("when"),
                "web_url": web_url,
            }
        )

    return json.dumps(
        {
            "results": results,
            "start": payload.get("start", start),
            "limit": payload.get("limit", limit),
            "size": payload.get("size", len(results)),
        },
        ensure_ascii=False,
    )
