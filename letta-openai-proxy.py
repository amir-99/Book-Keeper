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
    return connection_type(UPSTREAM.hostname, UPSTREAM.port, timeout=600)


def _created_timestamp(value: object) -> int:
    if not isinstance(value, str):
        return int(time.time())
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return int(time.time())


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

            content_parts: list[str] = []
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
                        content_parts.append(text)

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
                            "message": {"role": "assistant", "content": "".join(content_parts)},
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

    def _proxy(self) -> None:
        body = self._request_body()
        if self.command == "POST" and self.path.rstrip("/") == "/v1/chat/completions" and self._non_streaming_completion(body):
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
