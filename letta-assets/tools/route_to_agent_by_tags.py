def route_to_agent_by_tags(message: str, match_all: list[str]) -> str:
    """Send a task to exactly one local Letta agent matching every supplied tag.

    The call waits for the selected worker's assistant response. It returns a
    routing error instead of broadcasting when zero or multiple agents match.

    Args:
        message: Self-contained task and context for the selected worker.
        match_all: Tags that the selected worker must possess.

    Returns:
        A JSON object containing the worker name and its response, or a concise
        ROUTING_ERROR string.
    """
    import json
    import os
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode
    from urllib.request import Request, urlopen

    api_key = os.getenv("LETTA_API_KEY")
    sender_agent_id = os.getenv("LETTA_AGENT_ID")
    if not api_key:
        return "ROUTING_ERROR: LETTA_API_KEY is unavailable to the routing tool"
    if not sender_agent_id:
        return "ROUTING_ERROR: LETTA_AGENT_ID is unavailable to the routing tool"
    if not match_all or any(not isinstance(tag, str) or not tag for tag in match_all):
        return "ROUTING_ERROR: match_all must contain at least one non-empty tag"

    base_url = "http://localhost:8283"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "letta-local-agent-router/1.0",
    }

    query = urlencode(
        [("limit", "100"), ("match_all_tags", "true")]
        + [("tags", tag) for tag in match_all]
    )
    try:
        list_request = Request(
            f"{base_url}/v1/agents/?{query}", method="GET", headers=headers
        )
        with urlopen(list_request, timeout=60) as response:
            agents_payload = json.loads(response.read())
    except HTTPError as error:
        return f"ROUTING_ERROR: local Letta API returned HTTP {error.code} while listing workers"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"ROUTING_ERROR: unable to list local workers ({type(error).__name__})"

    if isinstance(agents_payload, dict):
        agents = agents_payload.get("agents", agents_payload.get("data", []))
    else:
        agents = agents_payload
    if not isinstance(agents, list):
        return "ROUTING_ERROR: local Letta API returned an invalid worker list"

    matches = [
        agent
        for agent in agents
        if isinstance(agent, dict)
        and isinstance(agent.get("id"), str)
        and agent["id"] != sender_agent_id
    ]
    if len(matches) != 1:
        return f"ROUTING_ERROR: expected exactly one matching worker, found {len(matches)}"

    worker = matches[0]
    augmented_message = (
        "[Delegated task from the user-facing router. Return an end-user-ready "
        f"answer; do not discuss routing.]\n\n{message}"
    )
    try:
        worker_body = json.dumps(
            {
                "messages": [
                    {"role": "system", "content": augmented_message}
                ]
            }
        ).encode()
        worker_request = Request(
            f"{base_url}/v1/agents/{quote(worker['id'], safe='')}/messages",
            data=worker_body,
            method="POST",
            headers=headers,
        )
        # Return a controlled routing failure before the 900-second Letta tool
        # sandbox kills this caller. Equal timeouts race at the outer boundary.
        with urlopen(worker_request, timeout=840) as response:
            response_payload = json.loads(response.read())
    except HTTPError as error:
        return f"ROUTING_ERROR: local Letta API returned HTTP {error.code} while running worker"
    except (URLError, OSError, ValueError, json.JSONDecodeError) as error:
        return f"ROUTING_ERROR: unable to run local worker ({type(error).__name__})"

    response_messages = (
        response_payload.get("messages", [])
        if isinstance(response_payload, dict)
        else []
    )
    assistant_messages: list[str] = []
    for response_message in response_messages:
        if (
            not isinstance(response_message, dict)
            or response_message.get("message_type") != "assistant_message"
        ):
            continue
        content = response_message.get("content")
        if isinstance(content, str) and content:
            assistant_messages.append(content)
        elif isinstance(content, list):
            text_parts = [
                part["text"]
                for part in content
                if isinstance(part, dict) and isinstance(part.get("text"), str)
            ]
            if text_parts:
                assistant_messages.append("\n".join(text_parts))

    if not assistant_messages:
        return "ROUTING_ERROR: selected worker returned no assistant response"
    return json.dumps(
        {
            "worker": worker.get("name") or "unnamed-worker",
            "response": assistant_messages,
        }
    )
