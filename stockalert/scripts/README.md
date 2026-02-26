# Scripts

## Schwab API (real data)

### 1. Get a refresh token (one-time)

The Schwab API uses OAuth. You need a **refresh token** before the app can get access tokens and fetch data. **Schwab requires callback URLs to be HTTPS** — that’s why `http://localhost:8765/callback` is rejected.

**You only need one callback URL.** If you already have `https://127.0.0.1:8080/oauth/callback` in the Developer Portal, use that. You do **not** need to add a second one (and you should not add `http://localhost:...` because it’s not HTTPS).

**Option A – Use your existing callback** (`https://127.0.0.1:8080/oauth/callback`)

1. In `.env` set:
   - `SCHWAB_CLIENT_ID` (your app key)
   - `SCHWAB_CLIENT_SECRET` (your app secret)
   - `SCHWAB_CALLBACK_URL=https://127.0.0.1:8080/oauth/callback`
2. When you run the OAuth flow, something must be serving **HTTPS** on `127.0.0.1:8080` and handling `/oauth/callback` (e.g. your FastAPI app). That handler should exchange the `?code=...` for tokens and save or print the refresh token. If your app already does that, just open the authorize URL (with `redirect_uri=https://127.0.0.1:8080/oauth/callback`), sign in, and use the token your app receives.

**Option B – Use ngrok** (no app or local HTTPS needed)

Use this if you don’t have an app on 8080 or don’t want to run it for this step:

1. Install [ngrok](https://ngrok.com/) and run: `ngrok http 8765`
2. Copy the **HTTPS** URL (e.g. `https://abc123.ngrok-free.app`).
3. In the [Schwab Developer Portal](https://developer.schwab.com/), add: `https://YOUR_NGROK_SUBDOMAIN.ngrok-free.app/callback` (you can have both this and `https://127.0.0.1:8080/oauth/callback` in the list; use whichever you prefer for this one-time flow).
4. In `.env` set `SCHWAB_CALLBACK_URL=https://YOUR_NGROK_SUBDOMAIN.ngrok-free.app/callback` (and `SCHWAB_CLIENT_ID`, `SCHWAB_CLIENT_SECRET`).
5. Leave ngrok running and run: `poetry run python scripts/schwab_get_refresh_token.py`
6. Open the URL printed, sign in, approve; the script will print `SCHWAB_REFRESH_TOKEN=...`. Add that to `.env`.

### 2. Test the API with real data

After `SCHWAB_REFRESH_TOKEN` is in `.env`:

```bash
poetry run python scripts/test_schwab_live.py
```

Optional arguments:

- `--symbol AAPL` (default: SPY)
- `--days 2` (default: 1)

This checks:

1. Token exchange (refresh token → access token)
2. User principals (streamer connection info)
3. Historical 1-min bars for the symbol

You can also run **"Schwab: Live API test (real keys)"** from the VS Code Run and Debug panel (uses SPY, 1 day).
