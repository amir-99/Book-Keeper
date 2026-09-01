def gitlab_list_my_merge_requests(
    state: str = "opened",
    relationships: str = "created_by_me,assigned_to_me,reviews_for_me",
    search: str = "",
    detail_limit: int = 100,
) -> str:
    """List merge requests related to the authenticated GitLab user across projects.

    This instance-wide lookup avoids enumerating projects. By default it
    combines merge requests created by the current user, assigned to the
    current user, and awaiting the current user's review. The reviewer lookup
    resolves the authenticated user ID for compatibility with GitLab versions
    that predate the ``reviews_for_me`` scope. Overlapping merge requests are
    returned once with every matching relationship.

    Args:
        state: opened, closed, locked, merged, or all.
        relationships: Comma-separated subset of created_by_me,
            assigned_to_me, and reviews_for_me.
        search: Optional text matched against merge-request title and description.
        detail_limit: Maximum deduplicated merge-request details to return, from
            1 through 100. Counts cover all fetched pages independently of this limit.

    Returns:
        JSON containing an exact deduplicated count when pagination completes,
        per-relationship counts, compact merge-request details, and pagination
        completeness, or a GITLAB_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import urlencode, urlsplit
    from urllib.request import Request, urlopen

    allowed_relationships = {
        "created_by_me",
        "assigned_to_me",
        "reviews_for_me",
    }
    if state not in {"opened", "closed", "locked", "merged", "all"}:
        return "GITLAB_ERROR: state must be opened, closed, locked, merged, or all"
    if not isinstance(relationships, str) or not relationships.strip() or len(relationships) > 200:
        return "GITLAB_ERROR: relationships must be a non-empty comma-separated string up to 200 characters"
    relationship_values = []
    for value in relationships.split(","):
        normalized = value.strip()
        if normalized and normalized not in relationship_values:
            relationship_values.append(normalized)
    unsupported = sorted(set(relationship_values) - allowed_relationships)
    if unsupported:
        return "GITLAB_ERROR: unsupported relationships: " + ", ".join(unsupported)
    if not relationship_values:
        return "GITLAB_ERROR: relationships must select at least one supported relationship"
    if not isinstance(search, str) or len(search) > 2000:
        return "GITLAB_ERROR: search must be a string up to 2000 characters"
    if not isinstance(detail_limit, int) or isinstance(detail_limit, bool) or not 1 <= detail_limit <= 100:
        return "GITLAB_ERROR: detail_limit must be an integer from 1 through 100"

    base_url = os.getenv("GITLAB_BASE_URL", "").strip().rstrip("/")
    token = os.getenv("GITLAB_ACCESS_TOKEN", "")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.netloc:
        return "GITLAB_ERROR: GITLAB_BASE_URL must be an HTTPS URL"
    if not token:
        return "GITLAB_ERROR: GITLAB_ACCESS_TOKEN is unavailable"

    per_relationship_counts = {}
    unique_merge_requests = {}
    count_is_exact = True
    max_pages_per_relationship = 100
    per_page = 100

    current_user_id = None
    if "reviews_for_me" in relationship_values:
        user_request = Request(
            f"{base_url}/api/v4/user",
            headers={
                "Accept": "application/json",
                "PRIVATE-TOKEN": token,
                "User-Agent": "letta-gitlab-tools/1.0",
            },
        )
        try:
            with urlopen(user_request, timeout=30) as response:
                user_payload = json.loads(response.read())
        except HTTPError as error:
            return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while resolving the authenticated reviewer"
        except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
            return f"GITLAB_ERROR: unable to resolve the authenticated reviewer ({type(error).__name__})"
        if not isinstance(user_payload, dict) or not isinstance(user_payload.get("id"), int):
            return "GITLAB_ERROR: GitLab returned an invalid authenticated user"
        current_user_id = user_payload["id"]

    for relationship in relationship_values:
        page = 1
        relationship_count = 0
        pagination_complete = False
        while page <= max_pages_per_relationship:
            params = {
                "state": state,
                "non_archived": "true",
                "order_by": "updated_at",
                "sort": "desc",
                "per_page": per_page,
                "page": page,
            }
            if relationship == "reviews_for_me":
                params["scope"] = "all"
                params["reviewer_id"] = current_user_id
            else:
                params["scope"] = relationship
            if search.strip():
                params["search"] = search.strip()
            request = Request(
                f"{base_url}/api/v4/merge_requests?{urlencode(params)}",
                headers={
                    "Accept": "application/json",
                    "PRIVATE-TOKEN": token,
                    "User-Agent": "letta-gitlab-tools/1.0",
                },
            )
            try:
                with urlopen(request, timeout=30) as response:
                    payload = json.loads(response.read())
                    next_page_header = response.headers.get("X-Next-Page", "").strip()
            except HTTPError as error:
                return f"GITLAB_ERROR: GitLab returned HTTP {error.code} while listing merge requests for {relationship}"
            except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
                return f"GITLAB_ERROR: unable to list merge requests for {relationship} ({type(error).__name__})"
            if not isinstance(payload, list):
                return f"GITLAB_ERROR: GitLab returned invalid merge requests for {relationship}"

            relationship_count += len(payload)
            for item in payload:
                if not isinstance(item, dict):
                    continue
                identifier = item.get("id")
                if not isinstance(identifier, int):
                    identifier = (item.get("project_id"), item.get("iid"))
                existing = unique_merge_requests.get(identifier)
                if existing is None:
                    author = item.get("author") if isinstance(item.get("author"), dict) else {}
                    assignees = item.get("assignees") if isinstance(item.get("assignees"), list) else []
                    reviewers = item.get("reviewers") if isinstance(item.get("reviewers"), list) else []
                    references = item.get("references") if isinstance(item.get("references"), dict) else {}
                    existing = {
                        "id": item.get("id"),
                        "iid": item.get("iid"),
                        "project_id": item.get("project_id"),
                        "reference": references.get("full") or references.get("relative"),
                        "title": item.get("title"),
                        "state": item.get("state"),
                        "draft": item.get("draft"),
                        "source_branch": item.get("source_branch"),
                        "target_branch": item.get("target_branch"),
                        "author": author.get("username"),
                        "assignees": [
                            value.get("username")
                            for value in assignees
                            if isinstance(value, dict)
                        ],
                        "reviewers": [
                            value.get("username")
                            for value in reviewers
                            if isinstance(value, dict)
                        ],
                        "detailed_merge_status": item.get("detailed_merge_status"),
                        "has_conflicts": item.get("has_conflicts"),
                        "created_at": item.get("created_at"),
                        "updated_at": item.get("updated_at"),
                        "merged_at": item.get("merged_at"),
                        "web_url": item.get("web_url"),
                        "relationships": [],
                    }
                    unique_merge_requests[identifier] = existing
                if relationship not in existing["relationships"]:
                    existing["relationships"].append(relationship)

            if next_page_header:
                try:
                    next_page = int(next_page_header)
                except ValueError:
                    return "GITLAB_ERROR: GitLab returned an invalid merge-request pagination header"
                if next_page <= page:
                    return "GITLAB_ERROR: GitLab returned a non-advancing merge-request page"
                page = next_page
            elif len(payload) == per_page:
                page += 1
            else:
                pagination_complete = True
                break

        if not pagination_complete:
            count_is_exact = False
        per_relationship_counts[relationship] = relationship_count

    ordered = sorted(
        unique_merge_requests.values(),
        key=lambda item: item.get("updated_at") if isinstance(item.get("updated_at"), str) else "",
        reverse=True,
    )
    selected = ordered[:detail_limit]
    return json.dumps(
        {
            "state": state,
            "relationships": relationship_values,
            "per_relationship_counts": per_relationship_counts,
            "total_unique": len(ordered),
            "count_is_exact": count_is_exact,
            "merge_requests": selected,
            "returned": len(selected),
            "details_truncated": len(ordered) > len(selected),
            "max_pages_per_relationship": max_pages_per_relationship,
        },
        ensure_ascii=False,
    )
