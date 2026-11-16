#!/usr/bin/env python3
"""Get a YouTube OAuth refresh token for unattended uploads (run ONCE, on your own computer).

Uses Google's official "installed app / loopback" OAuth flow for Desktop-app clients
(with PKCE). Only the Python standard library is needed.

    python scripts/get_youtube_refresh_token.py

It asks for your OAuth Client ID and Client Secret (input is hidden), opens your browser
to sign in to the Google account that owns the YouTube channel, then prints the refresh
token ONCE. Paste it into GitHub as the secret YOUTUBE_REFRESH_TOKEN.
Nothing is saved to disk and nothing is sent anywhere except Google.
"""
from __future__ import annotations

import base64
import getpass
import hashlib
import http.server
import json
import os
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",     # upload videos, set thumbnails
    "https://www.googleapis.com/auth/youtube.force-ssl",  # upload captions, check existing uploads
]


def main() -> int:
    cid = os.environ.get("YOUTUBE_CLIENT_ID") or input("OAuth Client ID: ").strip()
    csec = os.environ.get("YOUTUBE_CLIENT_SECRET") or getpass.getpass("OAuth Client Secret (hidden): ").strip()
    if not cid or not csec:
        print("Client ID and secret are required.")
        return 1

    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    result: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if "code" in q or "error" in q:
                result.update({k: v[0] for k, v in q.items()})
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                msg = "Done. You can close this tab and return to the terminal." if "code" in q else \
                    f"Authorisation failed: {q.get('error', ['?'])[0]}"
                self.wfile.write(f"<h2>{msg}</h2>".encode())
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect = f"http://127.0.0.1:{server.server_port}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = AUTH_URL + "?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent", "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    print("\nOpening your browser. If it doesn't open, paste this URL into it:\n\n" + url + "\n")
    print("Sign in with the Google account that owns the YouTube channel.")
    print("If you see 'Google hasn't verified this app': click Advanced > Go to Market Brief Podcast (unsafe).")
    print("That warning is expected for your own unverified app.\n")
    webbrowser.open(url)
    while not result:
        threading.Event().wait(0.3)
    server.shutdown()
    if "error" in result or result.get("state") != state:
        print(f"Authorisation failed: {result.get('error', 'state mismatch')}")
        return 1

    data = urllib.parse.urlencode({
        "code": result["code"], "client_id": cid, "client_secret": csec, "redirect_uri": redirect,
        "grant_type": "authorization_code", "code_verifier": verifier,
    }).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TOKEN_URL, data=data), timeout=30) as r:
            tok = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"Token exchange failed: HTTP {e.code} {e.read()[:300]!r}")
        return 1
    rt = tok.get("refresh_token")
    if not rt:
        print("Google returned no refresh token. Remove the app's access at "
              "https://myaccount.google.com/permissions and run this again.")
        return 1
    print("=" * 70)
    print("Your refresh token (copy everything on the next line):\n")
    print(rt)
    print("\n" + "=" * 70)
    print("Add it to GitHub: repo > Settings > Secrets and variables > Actions >\n"
          "New repository secret > Name: YOUTUBE_REFRESH_TOKEN > paste > Add secret.")
    print("Then clear your terminal (it contains the token).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
