from __future__ import annotations

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).parent


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        if path == "/api/health":
            body = json.dumps({"status": "ok"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
        else:
            filename = {"/": "index.html", "/dashboard": "dashboard.html"}.get(
                path
            )
            if filename is None:
                filename = "app.js" if path == "/app.js" else None
            if filename is None:
                self.send_error(404)
                return
            body = (ROOT / filename).read_bytes()
            content_type = (
                "application/javascript" if filename.endswith(".js") else "text/html"
            )
            self.send_response(200)
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


parser = argparse.ArgumentParser()
parser.add_argument("--port", type=int, default=0)
arguments = parser.parse_args()
server = ThreadingHTTPServer(("127.0.0.1", arguments.port), Handler)
print(f"READY {server.server_address[1]}", flush=True)
server.serve_forever()
