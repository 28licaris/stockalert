#!/usr/bin/env python3
"""
One-time setup: get a Schwab refresh token via OAuth in the browser.

1. Add http://localhost:8765/callback to your app's Callback URL(s) in the Schwab Developer Portal.
2. Set in .env: SCHWAB_CLIENT_ID, SCHWAB_CLIENT_SECRET.
3. Run: poetry run python scripts/schwab_get_refresh_token.py
4. Open the URL printed; sign in to Schwab and approve the app.
5. You'll be redirected to localhost; the script will exchange the code for tokens and print SCHWAB_REFRESH_TOKEN.
6. Add SCHWAB_REFRESH_TOKEN=... to your .env and use it for test_schwab_live.py and the app.
"""
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.request import urlopen, Request
from urllib.error import HTTPError
import json

from dotenv import load_dotenv
load_dotenv()

import os
CLIENT_ID = os.getenv("SCHWAB_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("SCHWAB_CLIENT_SECRET", "")
REDIRECT_URI = "http://localhost:8765/callback"
BASE_URL = os.getenv("SCHWAB_BASE_URL", "https://api.schwabapi.com").rstrip("/")
AUTHORIZE_URL = f"{BASE_URL}/v1/oauth/authorize"
TOKEN_URL = f"{BASE_URL}/v1/oauth/token"

code_holder = []


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/callback":
            qs = parse_qs(parsed.query)
            code = (qs.get("code") or [None])[0]
            error = (qs.get("error") or [None])[0]
            if code:
                code_holder.append(code)
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><p>Authorization successful. You can close this tab and return to the terminal.</p></body></html>"
                )
            else:
                self.send_response(400)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                msg = f"Error: {error}" if error else "No code in callback"
                self.wfile.write(f"<html><body><p>{msg}</p></body></html>".encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def run_server():
    server = HTTPServer(("localhost", 8765), CallbackHandler)
    while not code_holder:
        server.handle_request()
    return code_holder[0]


def main():
    if not CLIENT_ID or not CLIENT_SECRET:
        print("Set SCHWAB_CLIENT_ID and SCHWAB_CLIENT_SECRET in .env")
        return
    url = f"{AUTHORIZE_URL}?client_id={CLIENT_ID}&redirect_uri={REDIRECT_URI}&response_type=code"
    print("1. Add this callback URL to your app in the Schwab Developer Portal:")
    print(f"   {REDIRECT_URI}")
    print()
    print("2. Open this URL in your browser (or we'll try to open it):")
    print(url)
    print()
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print("3. After you sign in and authorize, waiting for callback on http://localhost:8765/callback ...")
    code = run_server()
    if not code:
        print("No authorization code received.")
        return
    print("4. Exchanging code for tokens...")
    body = urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET,
    })
    req = Request(TOKEN_URL, data=body.encode(), method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
    except HTTPError as e:
        print(f"Token exchange failed: {e.code} {e.read().decode()}")
        return
    refresh = data.get("refresh_token")
    access = data.get("access_token")
    if not refresh:
        print("Response missing refresh_token:", data.keys())
        return
    print()
    print("Success. Add this to your .env file:")
    print()
    print(f"SCHWAB_REFRESH_TOKEN={refresh}")
    print()
    if access:
        print("(Access token also received; use SCHWAB_REFRESH_TOKEN for ongoing use.)")


if __name__ == "__main__":
    main()
