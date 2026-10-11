#!/usr/bin/env python3
"""Tiny in-memory stand-in for the WordPress REST API, for local end-to-end runs.

Implements just enough of /wp-json/wp/v2 for publish_posts.py:
    GET  /categories?slug=...
    GET  /posts?slug=...&status=any
    POST /posts

Failure injection (so retries and api_failed paths are exercised):
    --flaky-slug  : first N POSTs for this slug return 503, then succeed
    --reject-slug : POSTs for this slug return 400 rest_invalid_param

    python mock_wp_server.py --port 8089
    WP_URL=http://localhost:8089 WP_ALLOW_HTTP=1 WP_USER=bot WP_APP_PASSWORD=secret \
        python publish_posts.py articles.csv --min-interval 0.1
"""

from __future__ import annotations

import argparse
import base64
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

CATEGORIES = [
    {"id": 2, "slug": "blog", "name": "Blog"},
    {"id": 3, "slug": "guides", "name": "Guides"},
    {"id": 4, "slug": "news", "name": "News"},
]


class State:
    posts: list[dict[str, Any]] = []
    next_id = 100
    flaky_slug = ""
    flaky_failures = 2
    reject_slug = ""
    user = "bot"
    password = "secret"


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
        return user == State.user and pw == State.password

    def _route(self) -> tuple[str, dict[str, str]]:
        parsed = urlparse(self.path)
        path = parsed.path.removeprefix("/wp-json/wp/v2")
        return path, {k: v[0] for k, v in parse_qs(parsed.query).items()}

    def do_GET(self) -> None:  # noqa: N802
        path, q = self._route()
        if path == "/categories":
            return self._send(200, [c for c in CATEGORIES if c["slug"] == q.get("slug")])
        if path == "/posts":
            if not self._authorized():
                return self._send(401, {"code": "rest_not_logged_in", "message": "Auth required"})
            return self._send(200, [p for p in State.posts if p["slug"] == q.get("slug")])
        self._send(404, {"code": "rest_no_route", "message": "No route"})

    def do_POST(self) -> None:  # noqa: N802
        path, _ = self._route()
        if path != "/posts":
            return self._send(404, {"code": "rest_no_route", "message": "No route"})
        if not self._authorized():
            return self._send(
                401,
                {"code": "rest_cannot_create", "message": "Sorry, you are not allowed to create posts as this user."},
            )

        payload = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        slug = payload.get("slug", "")

        if slug == State.flaky_slug and State.flaky_failures > 0:
            State.flaky_failures -= 1
            return self._send(
                503, {"code": "service_unavailable", "message": "Temporarily overloaded"}, {"Retry-After": "1"}
            )
        if slug == State.reject_slug:
            return self._send(
                400,
                {"code": "rest_invalid_param", "message": "Invalid parameter(s): content (blocked by security plugin)"},
            )

        post = {
            "id": State.next_id,
            "slug": slug,
            "status": payload.get("status"),
            "date_gmt": payload.get("date_gmt"),
            "title": {"raw": payload.get("title")},
            "categories": payload.get("categories"),
            "meta": payload.get("meta", {}),
            "link": f"http://{self.headers.get('Host')}/?p={State.next_id}",
        }
        State.next_id += 1
        State.posts.append(post)
        self._send(201, post)

    def log_message(self, fmt: str, *args: Any) -> None:
        print("[mock-wp]", fmt % args)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, default=8089)
    p.add_argument("--flaky-slug", default="core-web-vitals-content-sites")
    p.add_argument("--flaky-failures", type=int, default=2)
    p.add_argument("--reject-slug", default="eeat-signals")
    args = p.parse_args()
    State.flaky_slug, State.flaky_failures, State.reject_slug = args.flaky_slug, args.flaky_failures, args.reject_slug
    print(f"mock WordPress on http://localhost:{args.port} (user={State.user} pass={State.password})")
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
