from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, HTTPServer


class StubAPIHandler(BaseHTTPRequestHandler):
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
                    "repos": [],
                    "total": 0,
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
            self._send_json(200, {"results": []})
        else:
            self._send_json(404, {"error": "not found"})

    def do_DELETE(self) -> None:
        if self.path == "/v1/repos":
            self._send_json(
                200, {"status": "deleted", "slug": "stub-slug", "chunks_deleted": 0}
            )
        else:
            self._send_json(404, {"error": "not found"})


def run_server(port: int = 9999) -> None:
    with HTTPServer(("127.0.0.1", port), StubAPIHandler) as httpd:
        httpd.serve_forever()
