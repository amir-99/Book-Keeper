def jira_get_issue_context(issue_key: str, include_epic: bool = True, max_text_chars: int = 8000) -> str:
    """Read one Jira issue, its parent epic, and any Confluence links on both.

    Resolves the epic without prior configuration: it prefers the native
    `parent` field, falls back to the instance's "Epic Link" custom field
    discovered from the field catalog, and finally to an epic-typed issue link.
    Confluence URLs are collected from both issues' descriptions and remote
    links, so a caller does not have to parse issue text itself.

    Args:
        issue_key: Jira issue key, for example SEN-206.
        include_epic: Resolve and read the parent epic when one exists.
        max_text_chars: Per-description limit, from 1000 through 50000.

    Returns:
        JSON containing the issue, its epic, and discovered Confluence URLs, or
        a JIRA_ERROR string.
    """
    import base64
    import json
    import os
    import re
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit
    from urllib.request import Request, urlopen

    if not isinstance(issue_key, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{1,19}-\d{1,10}", issue_key.strip()):
        return "JIRA_ERROR: issue_key must look like SEN-206"
    if not isinstance(include_epic, bool):
        return "JIRA_ERROR: include_epic must be a boolean"
    if not isinstance(max_text_chars, int) or isinstance(max_text_chars, bool) or not 1000 <= max_text_chars <= 50000:
        return "JIRA_ERROR: max_text_chars must be from 1000 through 50000"

    base_url = os.getenv("JIRA_BASE_URL", "").strip().rstrip("/")
    credential = os.getenv("JIRA_ACCESS_TOKEN", "")
    auth_mode = os.getenv("JIRA_AUTH_MODE", "bearer").strip().lower()
    parsed_base_url = urlsplit(base_url)
    if parsed_base_url.scheme != "https" or not parsed_base_url.netloc:
        return "JIRA_ERROR: JIRA_BASE_URL must be an HTTPS URL"
    if not credential:
        return "JIRA_ERROR: JIRA_ACCESS_TOKEN is unavailable"
    if auth_mode == "basic":
        authorization = "Basic " + base64.b64encode(credential.encode()).decode()
    elif auth_mode == "basic_encoded":
        authorization = "Basic " + credential
    else:
        authorization = "Bearer " + credential
    headers = {"Accept": "application/json", "Authorization": authorization, "User-Agent": "letta-jira-tools/1.0"}

    confluence_host = urlsplit(os.getenv("CONFLUENCE_BASE_URL", "").strip()).netloc.lower()
    url_pattern = re.compile(r"https?://[^\s\)\]\|>\"']+")
    base_fields = "summary,description,status,issuetype,priority,labels,parent,issuelinks"

    # The "Epic Link" custom field only exists on instances where the epic is not
    # the native parent, and its id differs per instance, so discover it here
    # rather than requiring it to be configured.
    epic_field_id = None
    if include_epic:
        try:
            with urlopen(Request(f"{base_url}/rest/api/2/field", headers=headers), timeout=30) as response:
                catalog = json.loads(response.read())
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
            catalog = []
        if isinstance(catalog, list):
            for field in catalog:
                if isinstance(field, dict) and (field.get("name") or "").strip().lower() == "epic link":
                    epic_field_id = field.get("id")
                    break

    story = None
    epic = None
    epic_key = None
    epic_source = None
    links = []
    pending = [issue_key.strip().upper()]

    while pending:
        current = pending.pop(0)
        is_story = story is None
        query = urlencode({"fields": base_fields + (f",{epic_field_id}" if epic_field_id and is_story else "")})
        try:
            with urlopen(Request(f"{base_url}/rest/api/2/issue/{quote(current, safe='')}?{query}", headers=headers), timeout=30) as response:
                payload = json.loads(response.read())
        except HTTPError as error:
            if is_story:
                return f"JIRA_ERROR: Jira returned HTTP {error.code} while reading {current}"
            epic = {"key": current, "resolved_via": epic_source, "error": f"epic could not be read (HTTP {error.code})"}
            continue
        except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
            if is_story:
                return f"JIRA_ERROR: unable to read {current} ({type(error).__name__})"
            epic = {"key": current, "resolved_via": epic_source, "error": f"epic could not be read ({type(error).__name__})"}
            continue
        if not isinstance(payload, dict):
            if is_story:
                return f"JIRA_ERROR: Jira returned an invalid issue for {current}"
            epic = {"key": current, "resolved_via": epic_source, "error": "epic could not be read"}
            continue

        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        issue_type = fields.get("issuetype") if isinstance(fields.get("issuetype"), dict) else {}
        status = fields.get("status") if isinstance(fields.get("status"), dict) else {}
        priority = fields.get("priority") if isinstance(fields.get("priority"), dict) else {}
        description = fields.get("description") if isinstance(fields.get("description"), str) else ""
        compact = {
            "key": payload.get("key"),
            "summary": fields.get("summary"),
            "issue_type": issue_type.get("name"),
            "status": status.get("name"),
            "priority": priority.get("name"),
            "labels": fields.get("labels") if isinstance(fields.get("labels"), list) else [],
            "description": description[:max_text_chars] + ("\n[TRUNCATED]" if len(description) > max_text_chars else ""),
            "web_url": f"{base_url}/browse/{payload.get('key')}" if payload.get("key") else "",
        }
        if is_story:
            story = compact
        else:
            compact["resolved_via"] = epic_source
            epic = compact

        # Jira stores Confluence links both inline in the description and as
        # remote links, so scan one combined text for both.
        scan_text = description
        try:
            with urlopen(Request(f"{base_url}/rest/api/2/issue/{quote(current, safe='')}/remotelink", headers=headers), timeout=30) as response:
                remote_links = json.loads(response.read())
        except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
            remote_links = []
        if isinstance(remote_links, list):
            for item in remote_links:
                target = item.get("object") if isinstance(item, dict) and isinstance(item.get("object"), dict) else {}
                if isinstance(target.get("url"), str):
                    scan_text += "\n" + target["url"]
        for url in url_pattern.findall(scan_text):
            url = url.rstrip(".,;")
            host = urlsplit(url).netloc.lower()
            if confluence_host and host != confluence_host:
                continue
            if not confluence_host and not re.search(r"/(wiki|display|pages)/", url):
                continue
            if url not in links:
                links.append(url)

        if is_story and include_epic:
            parent = fields.get("parent") if isinstance(fields.get("parent"), dict) else {}
            parent_fields = parent.get("fields") if isinstance(parent.get("fields"), dict) else {}
            parent_type = parent_fields.get("issuetype") if isinstance(parent_fields.get("issuetype"), dict) else {}
            if isinstance(parent.get("key"), str):
                epic_key = parent["key"]
                epic_source = "parent"
                if (parent_type.get("name") or "").strip().lower() not in {"epic", ""}:
                    epic_source = "parent (not an epic)"
            if not epic_key and epic_field_id and isinstance(fields.get(epic_field_id), str):
                epic_key = fields[epic_field_id]
                epic_source = f"epic link ({epic_field_id})"
            if not epic_key:
                for link in fields.get("issuelinks") if isinstance(fields.get("issuelinks"), list) else []:
                    if not isinstance(link, dict):
                        continue
                    for side in ("outwardIssue", "inwardIssue"):
                        target = link.get(side) if isinstance(link.get(side), dict) else {}
                        target_fields = target.get("fields") if isinstance(target.get("fields"), dict) else {}
                        target_type = target_fields.get("issuetype") if isinstance(target_fields.get("issuetype"), dict) else {}
                        if (target_type.get("name") or "").strip().lower() == "epic" and isinstance(target.get("key"), str):
                            epic_key = target["key"]
                            epic_source = "issue link"
                            break
                    if epic_key:
                        break
            if epic_key:
                pending.append(epic_key)

    return json.dumps(
        {
            "issue": story,
            "epic": epic,
            "epic_resolved": bool(epic and not epic.get("error")),
            "confluence_urls": links,
            "epic_link_field": epic_field_id,
        },
        ensure_ascii=False,
    )
