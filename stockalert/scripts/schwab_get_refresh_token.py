#!/usr/bin/env python3
"""
One-time setup: get a Schwab refresh token via OAuth in the browser.

Schwab requires callback URLs to be HTTPS. For local dev, use ngrok:

1. Run: ngrok http 8765
2. Copy the HTTPS URL (e.g. https://abc123.ngrok-free.app).
3. In Schwab Developer Portal, add: https://abc123.ngrok-free.app/callback
4. In .env set: SCHWAB_CALLBACK_URL=https://abc123.ngrok-free.app/callback
   (and SCHWAB_CLIENT_ID, SCHWAB_CLIENT_SECRET)
5. Run: poetry run python scripts/schwab_get_refresh_token.py
6. Open the URL printed; sign in and approve. The script will print SCHWAB_REFRESH_TOKEN.
7. Add SCHWAB_REFRESH_TOKEN=... to .env.
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
# Schwab requires HTTPS for callback URL. Use ngrok and set SCHWAB_CALLBACK_URL to e.g. https://xxx.ngrok-free.app/callback
REDIRECT_URI = os.getenv("SCHWAB_CALLBACK_URL", "").strip()
BASE_URL = os.getenv("SCHWAB_BASE_URL", "https://api.schwabapi.com").rstrip("/")
TOKEN_FILE = os.getenv("SCHWAB_REFRESH_TOKEN_FILE", "data/.schwab_refresh_token")
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
    if not REDIRECT_URI or not REDIRECT_URI.startswith("https://"):
        print("Schwab requires an HTTPS callback URL.")
        print("Set SCHWAB_CALLBACK_URL in .env to your HTTPS callback (e.g. from ngrok).")
        print()
        print("Example with ngrok:")
        print("  1. Run: ngrok http 8765")
        print("  2. Copy the https URL (e.g. https://abc123.ngrok-free.app)")
        print("  3. In Schwab Developer Portal, add: https://abc123.ngrok-free.app/callback")
        print("  4. In .env set: SCHWAB_CALLBACK_URL=https://abc123.ngrok-free.app/callback")
        return
    query = urlencode({"client_id": CLIENT_ID, "redirect_uri": REDIRECT_URI, "response_type": "code"})
    url = f"{AUTHORIZE_URL}?{query}"
    print("1. Callback URL (must be added in Schwab Developer Portal):")
    print(f"   {REDIRECT_URI}")
    print()
    print("2. Open this URL in your browser (or we'll try to open it):")
    print(url)
    print()
    try:
        webbrowser.open(url)
    except Exception:
        pass
    print("3. After you sign in and authorize, waiting for callback...")
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
    # Write to token file so the app can use it without editing .env
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
    if access:
        print("(Access token also received; the app will use the refresh token for ongoing access.)")


if __name__ == "__main__":
    main()
