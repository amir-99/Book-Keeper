"""OpenAI compatibility adapter for Open WebUI -> Letta."""

from __future__ import annotations

import http.client
import json
import os
import queue
import re
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import Request, urlopen


UPSTREAM = urlsplit(os.environ.get("LETTA_UPSTREAM_URL", "http://letta:8283"))
API_KEY = os.environ["LETTA_API_KEY"]
PORT = int(os.environ.get("PROXY_PORT", "8081"))
HIDDEN_MODEL_TAG = os.environ.get("LETTA_HIDDEN_MODEL_TAG", "openwebui-hidden")
STREAM_MODE = os.environ.get("PROXY_STREAM_MODE", "native").strip().lower()
REVIEW_BASE_URL = os.environ.get("REVIEW_BASE_URL", "").strip().rstrip("/")
REVIEW_API_KEY = os.environ.get("REVIEW_API_KEY", "")
REVIEW_EVENT_POLL_SECONDS = float(os.environ.get("REVIEW_EVENT_POLL_SECONDS", "2"))
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _connection() -> http.client.HTTPConnection:
    connection_type = http.client.HTTPSConnection if UPSTREAM.scheme == "https" else http.client.HTTPConnection
    return connection_type(UPSTREAM.hostname, UPSTREAM.port, timeout=900)


def _created_timestamp(value: object) -> int:
    if not isinstance(value, str):
        return int(time.time())
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return int(time.time())


THINK_OPEN = "<think>"
THINK_CLOSE = "</think>"


def _held_length(text: str, tag: str) -> int:
    """Length of the trailing slice of ``text`` that could still grow into ``tag``."""
    for size in range(min(len(text), len(tag) - 1), 0, -1):
        if tag.startswith(text[-size:]):
            return size
    return 0


class ReasoningSplitter:
    """Route ``<think>...</think>`` from the content channel to ``reasoning_content``.

    Letta streams the agent's `<think>` preamble as ordinary content, one model
    token at a time, so `<think>` reaches Open WebUI split across chunks such as
    `<th`, `ink`, `>I`. Open WebUI does detect the tag server-side, but the
    events it forwards to the browser carry the raw chunk text, and it only
    rebuilds the message into a reasoning block once the response is finalized.
    The tags are therefore visible verbatim for the whole time the message is
    still streaming. Its `delta.reasoning_content` path has no such gap: the
    reasoning item is created on the first token and the browser is sent clean
    reasoning deltas, so translating here renders a live thinking block.

    Text buffered inside an unterminated block is flushed as reasoning, matching
    what Open WebUI does with a `<think>` that never closes.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._inside = False
        self._content_started = False

    def feed(self, text: str) -> list[tuple[str, str]]:
        self._buffer += text
        segments: list[tuple[str, str]] = []
        while True:
            tag = THINK_CLOSE if self._inside else THINK_OPEN
            channel = "reasoning_content" if self._inside else "content"
            position = self._buffer.find(tag)
            if position != -1:
                self._add(segments, channel, self._buffer[:position])
                self._buffer = self._buffer[position + len(tag) :]
                self._inside = not self._inside
                continue
            # Hold back a trailing partial tag so it is never emitted as content.
            keep = len(self._buffer) - _held_length(self._buffer, tag)
            self._add(segments, channel, self._buffer[:keep])
            self._buffer = self._buffer[keep:]
            return segments

    def flush(self) -> list[tuple[str, str]]:
        segments: list[tuple[str, str]] = []
        self._add(segments, "reasoning_content" if self._inside else "content", self._buffer)
        self._buffer = ""
        return segments

    def _add(self, segments: list[tuple[str, str]], channel: str, text: str) -> None:
        if not text:
            return
        if channel == "content":
            if not self._content_started:
                # Drop the newline the model leaves between </think> and the answer.
                text = text.lstrip()
                if not text:
                    return
            self._content_started = True
        segments.append((channel, text))


def rewrite_chunk(chunk: dict, splitters: dict[object, ReasoningSplitter]) -> list[dict]:
    """Expand one upstream chunk into chunks whose deltas use the right channel."""
    choices = chunk.get("choices")
    if not isinstance(choices, list) or not choices:
        return [chunk]

    rewritten: list[tuple[int, list[tuple[str, str]]]] = []
    for position, choice in enumerate(choices):
        if not isinstance(choice, dict):
            continue
        delta = choice.get("delta")
        text = delta.get("content") if isinstance(delta, dict) else None
        finished = choice.get("finish_reason") is not None
        if not isinstance(text, str):
            text = ""
        if not text and not finished:
            continue
        splitter = splitters.setdefault(choice.get("index", position), ReasoningSplitter())
        segments = splitter.feed(text) if text else []
        if finished:
            segments += splitter.flush()
        rewritten.append((position, segments))

    if not rewritten:
        return [chunk]

    events = []
    count = max(max((len(segments) for _, segments in rewritten), default=1), 1)
    for step in range(count):
        last = step == count - 1
        event = json.loads(json.dumps(chunk))
        for position, choice in enumerate(event["choices"]):
            if not isinstance(choice, dict):
                continue
            if not last:
                choice["finish_reason"] = None
            if step:
                # role and any non-content delta fields belong to the first event only.
                choice["delta"] = {}
        for position, segments in rewritten:
            choice = event["choices"][position]
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                delta = {}
                choice["delta"] = delta
            delta["content"] = None
            if step < len(segments):
                channel, text = segments[step]
                delta[channel] = text
        events.append(event)
    return events


def sse_event(chunk: dict) -> bytes:
    return b"data: " + json.dumps(chunk, separators=(",", ":")).encode() + b"\n\n"


def completion_chunk(
    completion_id: str, model: str, delta: dict[str, object],
    finish_reason: str | None = None,
) -> dict:
    """Build one OpenAI-shaped chat-completion stream chunk."""
    return {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


def native_request(payload: dict) -> tuple[str, dict, bool]:
    """Map Letta's OpenAI request contract to its native messages stream.

    The upstream compatibility route processes only the last user message
    because the agent owns its conversation state. Native mode preserves that
    behavior exactly.
    """
    model = payload.get("model")
    messages = payload.get("messages")
    if not isinstance(model, str) or not model:
        raise ValueError("model must identify a Letta agent")
    if not isinstance(messages, list):
        raise ValueError("messages must be a list")
    last_user = None
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role", "user") == "user":
            last_user = message
            break
    if last_user is None:
        raise ValueError("messages must include a user message")
    request = {
        "messages": [{"role": "user", "content": last_user.get("content", "")}],
        "stream_tokens": True,
        "include_pings": True,
    }
    return model, request, payload.get("stream") is not False


def native_text(value: object, field: str) -> str:
    """Extract string or typed text-list content from a native event."""
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts = []
    for item in value:
        if not isinstance(item, dict):
            continue
        text = item.get(field, item.get("text"))
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts)


def elapsed_label(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}:{remainder:02d}"


def route_details(arguments: str) -> tuple[str, str | None] | None:
    """Return a safe progress label and review id from completed router args."""
    try:
        payload = json.loads(arguments)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    message = payload.get("message")
    if not isinstance(message, str):
        return None
    mode_match = re.match(r"MODE: ([A-Z_]+)(?:\r?\n|$)", message)
    if not mode_match:
        return None
    mode = mode_match.group(1)
    review_match = re.search(r"(?:^|\n)REVIEW: (rev_[0-9a-f]{32})(?:\r?\n|$)", message)
    review_id = review_match.group(1) if review_match else None
    labels = {
        "EVIDENCE_GATHER": "Reading GitLab evidence",
        "WORKSPACE_OPEN": "Preparing the pinned repository",
        "TICKET_CONTEXT": "Resolving ticket and requirement context",
        "WORKSPACE_DISCARD": "Discarding the review workspace",
        "STAGE_DRAFTS": "Staging selected draft comments",
        "PUBLISH_REVIEW": "Publishing the confirmed review",
        "DISCARD_DRAFTS": "Discarding draft comments",
    }
    label = labels.get(mode, "Running delegated review stage")
    if mode == "REVIEW_ANALYSIS":
        tags = payload.get("match_all")
        tags = tags if isinstance(tags, list) else []
        if "routing-tier-large" in tags:
            label = "Running the deep analyst (Claude Opus 5)"
        elif "routing-tier-small" in tags:
            label = "Running the quick analyst (Gemini 3.7 Flash)"
        else:
            label = "Running the standard analyst (Claude Sonnet 5)"
    return label, review_id


def poll_review_events(review_id: str, after: int) -> tuple[list[dict], int]:
    """Read safe progress events without surfacing review-service failures."""
    if not REVIEW_BASE_URL or not REVIEW_API_KEY:
        return [], after
    query = urlencode({"after": after})
    request = Request(
        f"{REVIEW_BASE_URL}/reviews/{quote(review_id, safe='')}/events?{query}",
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {REVIEW_API_KEY}",
            "User-Agent": "letta-openai-proxy/1.0",
        },
    )
    try:
        with urlopen(request, timeout=3) as response:
            payload = json.loads(response.read())
    except (HTTPError, URLError, OSError, ValueError, json.JSONDecodeError):
        return [], after
    events = payload.get("events") if isinstance(payload, dict) else None
    cursor = payload.get("cursor") if isinstance(payload, dict) else None
    if not isinstance(events, list) or not isinstance(cursor, int):
        return [], after
    safe_events = [
        event for event in events
        if isinstance(event, dict)
        and isinstance(event.get("cursor"), int)
        and isinstance(event.get("stage"), str)
        and isinstance(event.get("detail"), str)
    ]
    return safe_events, max(after, cursor)


class NativeTranslator:
    """Translate typed Letta events into content/reasoning segments and beats."""

    def __init__(self) -> None:
        self.splitter = ReasoningSplitter()
        self.calls: dict[str, dict[str, object]] = {}
        self.last_call_id: str | None = None
        self.review_id: str | None = None
        self.finished = False

    def feed(self, event: dict) -> list[tuple[str, str]]:
        message_type = event.get("message_type")
        if message_type == "reasoning_message":
            text = native_text(event.get("reasoning"), "reasoning")
            return [("reasoning_content", text)] if text else []
        if message_type == "assistant_message":
            text = native_text(event.get("content"), "content")
            return self.splitter.feed(text) if text else []
        if message_type == "tool_call_message":
            return self._tool_call(event)
        if message_type == "tool_return_message":
            return self._tool_return(event)
        if message_type == "stop_reason":
            self.finished = True
            return self.splitter.flush()
        return []

    def finish(self) -> list[tuple[str, str]]:
        if self.finished:
            return []
        self.finished = True
        return self.splitter.flush()

    def _tool_call(self, event: dict) -> list[tuple[str, str]]:
        values = event.get("tool_calls")
        if isinstance(values, list):
            calls = [value for value in values if isinstance(value, dict)]
        elif isinstance(values, dict):
            calls = [values]
        else:
            value = event.get("tool_call")
            calls = [value] if isinstance(value, dict) else []
        beats: list[tuple[str, str]] = []
        for value in calls:
            supplied_call_id = value.get("tool_call_id")
            if isinstance(supplied_call_id, str) and supplied_call_id:
                call_id = supplied_call_id
                if (
                    self.last_call_id
                    and self.last_call_id.startswith("pending-")
                    and self.last_call_id in self.calls
                    and call_id not in self.calls
                ):
                    self.calls[call_id] = self.calls.pop(self.last_call_id)
            else:
                call_id = self.last_call_id or f"pending-{len(self.calls)}"
            self.last_call_id = call_id
            state = self.calls.setdefault(
                call_id,
                {"name": "", "arguments": "", "started": None, "label": None},
            )
            name = value.get("name")
            arguments = value.get("arguments")
            if isinstance(name, str):
                state["name"] = str(state["name"]) + name
            if isinstance(arguments, str):
                state["arguments"] = str(state["arguments"]) + arguments
            if state["name"] != "route_to_agent_by_tags" or state["started"] is not None:
                continue
            details = route_details(str(state["arguments"]))
            if details is None:
                continue
            label, review_id = details
            state["label"] = label
            state["started"] = time.monotonic()
            if review_id:
                self.review_id = review_id
            beats.append(("reasoning_content", f"{label}…\n"))
        return beats

    def _tool_return(self, event: dict) -> list[tuple[str, str]]:
        values = event.get("tool_returns")
        if isinstance(values, list):
            returns = [value for value in values if isinstance(value, dict)]
        else:
            returns = [event]
        beats: list[tuple[str, str]] = []
        for value in returns:
            call_id = value.get("tool_call_id")
            if not isinstance(call_id, str):
                call_id = self.last_call_id
            state = self.calls.get(call_id or "")
            if not state or state.get("name") != "route_to_agent_by_tags":
                continue
            if state.get("started") is None:
                details = route_details(str(state.get("arguments", "")))
                if details is None:
                    continue
                label, review_id = details
                state["label"] = label
                state["started"] = time.monotonic()
                if review_id:
                    self.review_id = review_id
                beats.append(("reasoning_content", f"{label}…\n"))
            label = str(state.get("label") or "Delegated review stage")
            duration = time.monotonic() - float(state["started"])
            status = value.get("status", event.get("status", "success"))
            verb = "completed in" if status == "success" else "failed after"
            beats.append(("reasoning_content", f"{label} {verb} {elapsed_label(duration)}.\n"))
            self.calls.pop(call_id or "", None)
        return beats


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, message: str, *args: object) -> None:
        print(f"{self.client_address[0]} - {message % args}", flush=True)

    def _request_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length) if length else b""

    def _upstream_headers(self, body: bytes) -> dict[str, str]:
        headers = {
            "Accept": self.headers.get("Accept", "application/json"),
            "Authorization": f"Bearer {API_KEY}",
            "Content-Length": str(len(body)),
            "Content-Type": self.headers.get("Content-Type", "application/json"),
            "User-Agent": self.headers.get("User-Agent", "letta-openai-proxy/1.0"),
        }
        for name in ("X-Project", "X-Request-Id"):
            if self.headers.get(name):
                headers[name] = self.headers[name]
        return headers

    def _open_upstream(self, method: str, path: str, body: bytes = b"") -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        connection = _connection()
        base_path = UPSTREAM.path.rstrip("/")
        connection.request(method, f"{base_path}{path}", body=body, headers=self._upstream_headers(body))
        return connection, connection.getresponse()

    def _send_bytes(self, status: int, body: bytes, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def _send_json(self, status: int, value: object) -> None:
        self._send_bytes(status, json.dumps(value, separators=(",", ":")).encode())

    def _models(self) -> None:
        connection = None
        try:
            connection, response = self._open_upstream("GET", "/v1/agents/?limit=1000")
            raw = response.read()
            if response.status != 200:
                self._send_bytes(response.status, raw, response.getheader("Content-Type", "application/json"))
                return

            payload = json.loads(raw)
            agents = payload.get("agents", payload.get("data", [])) if isinstance(payload, dict) else payload
            models = []
            for agent in agents if isinstance(agents, list) else []:
                tags = agent.get("tags") or []
                if isinstance(tags, list) and HIDDEN_MODEL_TAG in tags:
                    continue
                agent_id = agent.get("id")
                if agent_id:
                    models.append(
                        {
                            "id": agent_id,
                            "name": agent.get("name") or agent_id,
                            "object": "model",
                            "created": _created_timestamp(agent.get("created_at")),
                            "owned_by": "letta",
                        }
                    )
            self._send_json(200, {"object": "list", "data": models})
        except (OSError, ValueError, json.JSONDecodeError) as error:
            self._send_json(502, {"error": {"message": f"Unable to list Letta agents: {error}", "type": "upstream_error"}})
        finally:
            if connection:
                connection.close()

    def _non_streaming_completion(self, body: bytes) -> bool:
        try:
            request_payload = json.loads(body)
        except json.JSONDecodeError:
            return False
        if request_payload.get("stream") is not False:
            return False

        request_payload["stream"] = True
        upstream_body = json.dumps(request_payload, separators=(",", ":")).encode()
        connection = None
        try:
            connection, response = self._open_upstream("POST", "/v1/chat/completions", upstream_body)
            raw = response.read()
            if response.status != 200:
                self._send_bytes(response.status, raw, response.getheader("Content-Type", "application/json"))
                return True

            splitter = ReasoningSplitter()
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            completion_id = f"chatcmpl-{int(time.time() * 1000)}"
            for line in raw.splitlines():
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if not data or data == b"[DONE]":
                    continue
                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    continue
                completion_id = event.get("id", completion_id)
                choices = event.get("choices") or []
                if choices:
                    text = (choices[0].get("delta") or {}).get("content")
                    if isinstance(text, str):
                        for channel, segment in splitter.feed(text):
                            target = reasoning_parts if channel == "reasoning_content" else content_parts
                            target.append(segment)
            for channel, segment in splitter.flush():
                target = reasoning_parts if channel == "reasoning_content" else content_parts
                target.append(segment)

            message = {"role": "assistant", "content": "".join(content_parts)}
            if reasoning_parts:
                message["reasoning_content"] = "".join(reasoning_parts)

            self._send_json(
                200,
                {
                    "id": completion_id,
                    "object": "chat.completion",
                    "created": int(time.time()),
                    "model": request_payload.get("model", "letta"),
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": "stop",
                        }
                    ],
                },
            )
            return True
        except OSError as error:
            self._send_json(502, {"error": {"message": f"Letta request failed: {error}", "type": "upstream_error"}})
            return True
        finally:
            if connection:
                connection.close()

    def _streaming_completion(self, body: bytes) -> bool:
        try:
            json.loads(body)
        except json.JSONDecodeError:
            return False

        connection = None
        try:
            connection, response = self._open_upstream("POST", "/v1/chat/completions", body)
            if response.status != 200:
                raw = response.read()
                self._send_bytes(response.status, raw, response.getheader("Content-Type", "application/json"))
                return True

            self.send_response(200)
            self.send_header("Content-Type", response.getheader("Content-Type", "text/event-stream"))
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "close")
            self.end_headers()

            splitters: dict[object, ReasoningSplitter] = {}
            for raw_line in response:
                line = raw_line.strip()
                if not line.startswith(b"data:"):
                    self.wfile.write(raw_line)
                    self.wfile.flush()
                    continue

                data = line[5:].strip()
                if data == b"[DONE]":
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                    continue

                try:
                    chunk = json.loads(data)
                except json.JSONDecodeError:
                    self.wfile.write(raw_line)
                    self.wfile.flush()
                    continue

                for event in rewrite_chunk(chunk, splitters) if isinstance(chunk, dict) else [chunk]:
                    self.wfile.write(sse_event(event))
                self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError):
            return True
        except OSError as error:
            if not self.wfile.closed:
                self._send_json(502, {"error": {"message": f"Letta request failed: {error}", "type": "upstream_error"}})
            return True
        finally:
            if connection:
                connection.close()
            self.close_connection = True

    def _native_completion(self, body: bytes) -> bool:
        try:
            request_payload = json.loads(body)
            if not isinstance(request_payload, dict):
                raise ValueError("request body must be a JSON object")
            model, native_payload, streaming = native_request(request_payload)
        except (ValueError, json.JSONDecodeError) as error:
            self._send_json(400, {"error": {"message": str(error), "type": "invalid_request_error"}})
            return True

        upstream_body = json.dumps(native_payload, separators=(",", ":")).encode()
        connection = None
        try:
            path = f"/v1/agents/{quote(model, safe='')}/messages/stream"
            connection, response = self._open_upstream("POST", path, upstream_body)
            if response.status != 200:
                raw = response.read()
                self._send_bytes(response.status, raw, response.getheader("Content-Type", "application/json"))
                return True

            completion_id = f"chatcmpl-{int(time.time() * 1000)}"
            translator = NativeTranslator()
            started = time.monotonic()
            content_parts: list[str] = []
            reasoning_parts: list[str] = []
            event_cursor = 0
            next_poll = started
            source: queue.Queue[object] = queue.Queue()

            def read_upstream() -> None:
                try:
                    for raw_line in response:
                        source.put(raw_line)
                except OSError as error:
                    source.put(error)
                finally:
                    source.put(None)

            reader = threading.Thread(target=read_upstream, name="letta-native-reader", daemon=True)
            reader.start()

            if streaming:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(sse_event(completion_chunk(completion_id, model, {"role": "assistant", "content": None})))
                self.wfile.flush()

            done = False
            upstream_failed = False
            while not done:
                now_value = time.monotonic()
                wait_for = 0.5
                if translator.review_id:
                    wait_for = max(0.05, min(wait_for, next_poll - now_value))
                try:
                    item = source.get(timeout=wait_for)
                except queue.Empty:
                    item = ...

                segments: list[tuple[str, str]] = []
                if item is None:
                    segments.extend(translator.finish())
                    done = True
                elif isinstance(item, OSError):
                    segments.extend(translator.finish())
                    upstream_failed = True
                    done = True
                elif isinstance(item, bytes):
                    line = item.strip()
                    if line.startswith(b"data:"):
                        data = line[5:].strip()
                        if data == b"[DONE]":
                            segments.extend(translator.finish())
                            done = True
                        elif data:
                            try:
                                native_event = json.loads(data)
                            except json.JSONDecodeError:
                                native_event = None
                            if isinstance(native_event, dict):
                                if native_event.get("message_type") == "error_message":
                                    upstream_failed = True
                                segments.extend(translator.feed(native_event))

                if translator.review_id and time.monotonic() >= next_poll:
                    events, event_cursor = poll_review_events(translator.review_id, event_cursor)
                    for event in events:
                        elapsed = elapsed_label(time.monotonic() - started)
                        segments.append(
                            ("reasoning_content", f"{elapsed} · {event['stage']} · {event['detail']}\n")
                        )
                    next_poll = time.monotonic() + REVIEW_EVENT_POLL_SECONDS

                for channel, text in segments:
                    if not text:
                        continue
                    if streaming:
                        self.wfile.write(sse_event(completion_chunk(completion_id, model, {channel: text})))
                        self.wfile.flush()
                    elif channel == "reasoning_content":
                        reasoning_parts.append(text)
                    else:
                        content_parts.append(text)

            if streaming:
                finish_reason = "stop" if not upstream_failed else "error"
                self.wfile.write(sse_event(completion_chunk(completion_id, model, {}, finish_reason)))
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            else:
                message: dict[str, object] = {"role": "assistant", "content": "".join(content_parts)}
                if reasoning_parts:
                    message["reasoning_content"] = "".join(reasoning_parts)
                self._send_json(
                    200,
                    {
                        "id": completion_id,
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": model,
                        "choices": [{
                            "index": 0,
                            "message": message,
                            "finish_reason": "stop" if not upstream_failed else "error",
                        }],
                    },
                )
            return True
        except (BrokenPipeError, ConnectionResetError):
            return True
        except OSError as error:
            if not self.wfile.closed:
                self._send_json(502, {"error": {"message": f"Letta request failed: {error}", "type": "upstream_error"}})
            return True
        finally:
            if connection:
                connection.close()
            self.close_connection = True

    def _proxy(self) -> None:
        body = self._request_body()
        if self.command == "POST" and self.path.rstrip("/") == "/v1/chat/completions":
            if STREAM_MODE == "native":
                if self._native_completion(body):
                    return
            elif STREAM_MODE != "openai":
                self._send_json(500, {"error": {"message": "PROXY_STREAM_MODE must be native or openai", "type": "configuration_error"}})
                return
            if self._non_streaming_completion(body):
                return
            if self._streaming_completion(body):
                return

        connection = None
        try:
            connection, response = self._open_upstream(self.command, self.path, body)
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in HOP_BY_HOP_HEADERS and name.lower() != "content-length":
                    self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()

            while True:
                chunk = response.read1(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except OSError as error:
            if not self.wfile.closed:
                self._send_json(502, {"error": {"message": f"Letta request failed: {error}", "type": "upstream_error"}})
        finally:
            if connection:
                connection.close()
            self.close_connection = True

    def do_GET(self) -> None:  # noqa: N802
        if self.path.rstrip("/") == "/health":
            self._send_json(200, {"status": True})
        elif self.path.rstrip("/") == "/v1/models":
            self._models()
        else:
            self._proxy()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy()

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy()

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy()


if __name__ == "__main__":
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Letta OpenAI adapter listening on 0.0.0.0:{PORT}", flush=True)
    server.serve_forever()
