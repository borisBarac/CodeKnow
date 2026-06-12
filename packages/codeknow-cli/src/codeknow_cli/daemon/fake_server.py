from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import ClassVar

_STUB_REPO = {
    "github_ssh_url": "git@github.com:stub/repo.git",
    "slug": "stub-slug",
    "commit_hash": "abc123",
    "built_at": "2025-01-01T00:00:00Z",
    "node_count": 10,
    "edge_count": 20,
    "community_count": 2,
}


class StubAPIHandler(BaseHTTPRequestHandler):
    STUB_REPO: ClassVar[dict] = _STUB_REPO

    def _send_json(self, status: int, body: dict | list) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:
        if self.path.startswith("/v1/repos"):
            self._send_json(
                200,
                {
                    "repos": [self.STUB_REPO],
                    "total": 1,
                    "page": 1,
                    "page_size": 50,
                    "errors": [],
                },
            )
        elif self.path.startswith("/v1/build/"):
            slug = self.path[len("/v1/build/") :]
            self._send_json(
                200,
                {
                    "status": "succeeded",
                    "slug": slug,
                    "progress": 100,
                    "stage": None,
                    "message": None,
                    "commit_hash": "abc123",
                    "node_count": 10,
                    "edge_count": 20,
                    "community_count": 2,
                },
            )
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/v1/build":
            self._send_json(
                202,
                {
                    "status": "queued",
                    "slug": "stub-slug",
                    "status_url": "/v1/build/stub-slug",
                    "progress": 0,
                },
            )
        elif self.path == "/v1/search":
            self._send_json(
                200,
                {
                    "query": "test",
                    "vector_hits": 0,
                    "graph_expanded": 0,
                    "results": [],
                },
            )
        else:
            self._send_json(404, {"error": "not found"})

    def _read_json_body(self) -> dict[str, object]:
        length = int(self.headers.get("Content-Length", 0))
        if length:
            data: dict[str, object] = json.loads(self.rfile.read(length))
            return data
        return {}

    def do_DELETE(self) -> None:
        if self.path == "/v1/repos":
            body = self._read_json_body()
            slug = body.get("slug", "")
            if slug and slug != "stub-slug":
                self._send_json(404, {"detail": f"Repo with slug '{slug}' not found"})
            else:
                self._send_json(
                    200,
                    {
                        "status": "deleted",
                        "slug": slug or "stub-slug",
                        "chunks_deleted": 0,
                    },
                )
        else:
            self._send_json(404, {"error": "not found"})


def run_server(host: str = "127.0.0.1", port: int = 9999) -> None:
    with HTTPServer((host, port), StubAPIHandler) as httpd:
        httpd.serve_forever()
