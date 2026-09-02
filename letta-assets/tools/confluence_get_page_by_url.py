def confluence_get_page_by_url(page_url: str, body_format: str = "view", max_body_chars: int = 20000) -> str:
    """Read one Confluence page identified by a browser URL rather than an ID.

    Resolves the three URL shapes Confluence hands out: a `pageId` query
    parameter, a `/pages/<id>/` path segment, and a `/display/<SPACE>/<Title>`
    path that is resolved through a title search. Issue trackers store these
    links verbatim, so this avoids parsing them outside a tool.

    Args:
        page_url: Confluence page URL taken from an issue or document link.
        body_format: Body representation: storage, view, or export_view.
        max_body_chars: Maximum body characters returned, from 1000 to 100000.

    Returns:
        JSON containing page metadata and body content, or a CONFLUENCE_ERROR
        string. body_truncated reports whether output was cut.
    """
    import base64
    import json
    import os
    import re
    from urllib.error import HTTPError, URLError
    from urllib.parse import parse_qs, quote, unquote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(page_url, str) or not page_url.strip():
        return "CONFLUENCE_ERROR: page_url must be a non-empty string"
    if body_format not in {"storage", "view", "export_view"}:
        return "CONFLUENCE_ERROR: body_format must be storage, view, or export_view"
    if not isinstance(max_body_chars, int) or isinstance(max_body_chars, bool) or not 1000 <= max_body_chars <= 100000:
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

    parsed = urlsplit(page_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return "CONFLUENCE_ERROR: page_url must be an absolute HTTP(S) URL"
    if parsed.netloc.lower() != parsed_base_url.netloc.lower():
        return "CONFLUENCE_ERROR: page_url does not belong to the configured Confluence site"

    headers = {"Accept": "application/json", "Authorization": authorization, "User-Agent": "letta-confluence-tools/1.0"}

    page_id = ""
    resolved_via = ""
    query_page_id = parse_qs(parsed.query).get("pageId", [""])[0]
    if query_page_id.strip().isdigit():
        page_id, resolved_via = query_page_id.strip(), "pageId parameter"
    if not page_id:
        match = re.search(r"/pages/(\d+)(?:/|$)", parsed.path)
        if match:
            page_id, resolved_via = match.group(1), "pages path segment"
    if not page_id:
        match = re.search(r"/display/([^/]+)/([^/?#]+)", parsed.path)
        if not match:
            return "CONFLUENCE_ERROR: page_url does not identify a page by id, pages path, or display path"
        space = unquote(match.group(1))
        title = unquote(match.group(2)).replace("+", " ")
        cql = 'space = "{}" AND title = "{}"'.format(space.replace('"', ""), title.replace('"', ""))
        search_path = "/rest/api/content/search?" + urlencode({"cql": cql, "limit": 1})
        try:
            with urlopen(Request(f"{base_url}{search_path}", method="GET", headers=headers), timeout=30) as response:
                search = json.loads(response.read())
        except HTTPError as error:
            return f"CONFLUENCE_ERROR: Confluence returned HTTP {error.code} while searching for the page"
        except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
            return f"CONFLUENCE_ERROR: unable to search for the page ({type(error).__name__})"
        results = search.get("results") if isinstance(search, dict) and isinstance(search.get("results"), list) else []
        if not results or not isinstance(results[0], dict) or not results[0].get("id"):
            return f"CONFLUENCE_ERROR: no page titled {title!r} was found in space {space!r}"
        page_id, resolved_via = str(results[0]["id"]), "display path title search"

    query = urlencode({"expand": f"space,version,ancestors,body.{body_format}"})
    content_path = f"/rest/api/content/{quote(page_id, safe='')}?{query}"
    try:
        with urlopen(Request(f"{base_url}{content_path}", method="GET", headers=headers), timeout=30) as response:
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
            "space_key": space.get("key"),
            "version": version.get("number"),
            "updated_at": version.get("when"),
            "resolved_via": resolved_via,
            "body_format": body_format,
            "body": body_value,
            "body_truncated": body_truncated,
            "web_url": web_url,
        },
        ensure_ascii=False,
    )
