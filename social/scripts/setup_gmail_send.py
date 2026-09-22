#!/usr/bin/env python3
"""
One-time setup: let the Story scheduler email its run report.

The existing Gmail access (the gog CLI) is read-only, so it cannot send. This
runs a loopback OAuth flow against the same Internal desktop client, asks only
for gmail.send, and stores the resulting refresh token at
~/.config/grmr/gmail-send.json with permissions 600 — outside the repo, like
every other credential here.

Run it once, in a terminal, while signed in as linus@giantrockmeetingroom.com:

    python3 social/scripts/setup_gmail_send.py

A browser window opens for consent. Nothing is printed except whether it
worked; the token never appears on screen.
"""

from __future__ import annotations

import http.server
import json
import secrets
import socket
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path


CLIENT_FILE = Path.home() / ".config" / "grmr" / "gog-grmr-internal-client.json"
OUTPUT_FILE = Path.home() / ".config" / "grmr" / "gmail-send.json"
SCOPE = "https://www.googleapis.com/auth/gmail.send"
ACCOUNT = "linus@giantrockmeetingroom.com"

PAGE = b"""<html><body style="font-family:system-ui;padding:3rem">
<h2>Done.</h2><p>The scheduler can send its report now. You can close this tab.</p>
</body></html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    code: str | None = None
    state: str = ""

    def do_GET(self) -> None:  # noqa: N802 - name fixed by the base class
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        if query.get("state", [""])[0] == Handler.state:
            Handler.code = query.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *args: object) -> None:
        pass  # keep the terminal clean


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def main() -> None:
    if not CLIENT_FILE.exists():
        raise SystemExit(f"OAuth client not found at {CLIENT_FILE}")
    client = json.loads(CLIENT_FILE.read_text())["installed"]

    port = free_port()
    redirect_uri = f"http://localhost:{port}"
    Handler.state = secrets.token_urlsafe(16)

    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(
        {
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "login_hint": ACCOUNT,
            "state": Handler.state,
        }
    )

    print(f"Opening a browser to authorize sending as {ACCOUNT}.")
    print("If it does not open, paste this into a browser:\n")
    print(auth_url + "\n")
    webbrowser.open(auth_url)

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 300
    while Handler.code is None:
        server.handle_request()
        if Handler.code is None:
            print("Waiting for the browser...")

    response = urllib.request.urlopen(
        urllib.request.Request(
            client["token_uri"],
            data=urllib.parse.urlencode(
                {
                    "client_id": client["client_id"],
                    "client_secret": client["client_secret"],
                    "code": Handler.code,
                    "grant_type": "authorization_code",
                    "redirect_uri": redirect_uri,
                }
            ).encode(),
            method="POST",
        ),
        timeout=60,
    )
    tokens = json.loads(response.read().decode())
    if "refresh_token" not in tokens:
        raise SystemExit("Google did not return a refresh token. Re-run and approve the consent screen.")

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_FILE.write_text(
        json.dumps(
            {
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
                "refresh_token": tokens["refresh_token"],
                "to": ACCOUNT,
                "from": ACCOUNT,
            },
            indent=2,
        )
        + "\n"
    )
    OUTPUT_FILE.chmod(0o600)
    print(f"\nSaved to {OUTPUT_FILE} (permissions 600). The scheduler will email its reports.")


if __name__ == "__main__":
    main()
