#!/usr/bin/env python3
"""
One-time setup: get a Schwab refresh token via OAuth in the browser.

Supports two flows:

  Paste flow (no ngrok): Set SCHWAB_CALLBACK_URL to a local HTTPS URL
  (e.g. https://127.0.0.1:8080/oauth/callback). After you sign in, the browser
  will show "site can't be reached" — copy the full URL from the address bar
  and paste it when the script asks. The script extracts the code and saves
  the token. No ngrok or extra tools needed.

  Server flow (ngrok): Set SCHWAB_CALLBACK_URL to your ngrok HTTPS URL + /callback.
  Run ngrok http 8765, then run this script. After sign-in you'll see
  "Authorization successful" and the script will receive the token automatically.
"""
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs, urlencode
from urllib.request import urlopen, Request
from urllib.error import HTTPError
import json
import sys

from dotenv import load_dotenv
load_dotenv()

import base64
import os

CLIENT_ID = os.getenv("SCHWAB_CLIENT_ID", "")
CLIENT_SECRET = os.getenv("SCHWAB_CLIENT_SECRET", "")
REDIRECT_URI = os.getenv("SCHWAB_CALLBACK_URL", "").strip()
BASE_URL = os.getenv("SCHWAB_BASE_URL", "https://api.schwabapi.com").rstrip("/")
TOKEN_FILE = os.getenv("SCHWAB_REFRESH_TOKEN_FILE", "data/.schwab_refresh_token")
AUTHORIZE_URL = f"{BASE_URL}/v1/oauth/authorize"
TOKEN_URL = f"{BASE_URL}/v1/oauth/token"

code_holder = []


def _parse_code_from_redirect_url(redirect_url: str) -> str | None:
    """Extract authorization code from a full redirect URL (e.g. from browser address bar)."""
    s = redirect_url.strip()
    if not s:
        return None
    try:
        parsed = urlparse(s)
        qs = parse_qs(parsed.query)
        return (qs.get("code") or [None])[0]
    except Exception:
        return None


def exchange_code_for_tokens(code: str) -> dict:
    """Exchange authorization code for tokens. Raises on failure.
    Schwab's token endpoint expects client credentials via Basic auth (RFC 6749 2.3.1).
    """
    body = urlencode({
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT_URI,
    })
    credentials = base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
    req = Request(
        TOKEN_URL,
        data=body.encode(),
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Authorization": f"Basic {credentials}",
        },
    )
    with urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def save_refresh_token(refresh: str) -> None:
    """Write refresh token to token file; fallback to printing for .env."""
    try:
        os.makedirs(os.path.dirname(TOKEN_FILE) or ".", exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            f.write(refresh)
        print()
        print(f"Refresh token saved to {TOKEN_FILE}")
        print("The app will use this file automatically; you do not need to add SCHWAB_REFRESH_TOKEN to .env.")
        print()
    except OSError as e:
        print(f"Could not write token file: {e}")
        print()
        print("Add this to your .env file instead:")
        print(f"SCHWAB_REFRESH_TOKEN={refresh}")
        print()


class CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/callback" or parsed.path.endswith("/callback"):
            qs = parse_qs(parsed.query)
            code = (qs.get("code") or [None])[0]
            error = (qs.get("error") or [None])[0]
            if code:
                code_holder.append(code)
                self.send_response(200)
                self.send_header("Content-type", "text/html")
                self.end_headers()
                self.wfile.write(
                    b"<html><body><p><strong>Authorization successful.</strong> You can close this tab and return to the terminal. The script has received the token.</p></body></html>"
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


def run_server() -> str:
    server = HTTPServer(("localhost", 8765), CallbackHandler)
    while not code_holder:
        server.handle_request()
    return code_holder[0]


def _is_localhost_callback() -> bool:
    if not REDIRECT_URI:
        return False
    lower = REDIRECT_URI.lower()
    return "127.0.0.1" in lower or ("localhost" in lower and "ngrok" not in lower)


def main() -> None:
    if not CLIENT_ID or not CLIENT_SECRET:
        print("Set SCHWAB_CLIENT_ID and SCHWAB_CLIENT_SECRET in .env")
        sys.exit(1)
    if not REDIRECT_URI or not REDIRECT_URI.startswith("https://"):
        print("Schwab requires an HTTPS callback URL.")
        print("Set SCHWAB_CALLBACK_URL in .env to your ngrok HTTPS URL + /callback (e.g. https://abc.ngrok-free.app/callback).")
        sys.exit(1)

    use_paste = _is_localhost_callback()
    query = urlencode({"client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI, "response_type": "code"})
    url = f"{AUTHORIZE_URL}?{query}"

    print("Callback URL (must match Schwab Developer Portal):")
    print(f"  {REDIRECT_URI}")
    print()
    if use_paste:
        print("Mode: paste URL (no ngrok). After sign-in the browser will show \"site can't be reached\".")
        print("Copy the full URL from the address bar and paste it when prompted.")
    else:
        print("Mode: server. Ensure ngrok is running:  ngrok http 8765")
    print()
    print("Opening the authorize URL in your browser...")
    print(url)
    print()
    try:
        webbrowser.open(url)
    except Exception:
        pass

    if use_paste:
        print("After you sign in, the browser will redirect and the page may show \"connection refused\".")
        print("Copy the entire URL from the address bar (it contains code=...) and paste it below.")
        print()
        try:
            line = input("Paste redirect URL here: ").strip()
        except EOFError:
            print("No input; run again and paste the URL when prompted.")
            sys.exit(1)
        code = _parse_code_from_redirect_url(line)
        if not code:
            print("Could not find 'code' in the URL. Paste the full URL from the browser address bar.")
            sys.exit(1)
    else:
        print("Waiting for callback on port 8765...")
        code = run_server()
        if not code:
            print("No authorization code received.")
            sys.exit(1)

    print("Exchanging code for tokens...")
    try:
        data = exchange_code_for_tokens(code)
    except HTTPError as e:
        print(f"Token exchange failed: {e.code} {e.read().decode()}")
        sys.exit(1)

    refresh = data.get("refresh_token")
    if not refresh:
        print("Response missing refresh_token:", list(data.keys()))
        sys.exit(1)

    save_refresh_token(refresh)
    if data.get("access_token"):
        print("(Access token also received; the app will use the refresh token for ongoing access.)")


if __name__ == "__main__":
    main()
