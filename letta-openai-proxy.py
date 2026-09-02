"""OpenAI compatibility adapter for Open WebUI -> Letta."""

from __future__ import annotations

import http.client
import json
import os
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


UPSTREAM = urlsplit(os.environ.get("LETTA_UPSTREAM_URL", "http://letta:8283"))
API_KEY = os.environ["LETTA_API_KEY"]
PORT = int(os.environ.get("PROXY_PORT", "8081"))
HIDDEN_MODEL_TAG = os.environ.get("LETTA_HIDDEN_MODEL_TAG", "openwebui-hidden")
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

    def _proxy(self) -> None:
        body = self._request_body()
        if self.command == "POST" and self.path.rstrip("/") == "/v1/chat/completions":
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
