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
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        if self.path == "/v1/build":
            self._send_json(
                202,
                {
                    "status": "done",
                    "slug": "stub-slug",
                    "commit_hash": None,
                    "node_count": None,
                    "edge_count": None,
                    "community_count": None,
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

    def do_DELETE(self) -> None:
        if self.path == "/v1/repos":
            self._send_json(
                200, {"status": "deleted", "slug": "stub-slug", "chunks_deleted": 0}
            )
        else:
            self._send_json(404, {"error": "not found"})


def run_server(host: str = "127.0.0.1", port: int = 9999) -> None:
    with HTTPServer((host, port), StubAPIHandler) as httpd:
        httpd.serve_forever()
