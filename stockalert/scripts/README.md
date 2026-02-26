# Scripts

## Schwab API (real data)

**Credentials to test the Schwab data provider** — set these in `.env`:

| Variable | Required | Description |
|----------|----------|-------------|
| `DATA_PROVIDER` | Yes | Set to `schwab` or `thinkorswim` so the app uses Schwab. |
| `SCHWAB_CLIENT_ID` | Yes | App key (Client ID) from Schwab Developer Portal. |
| `SCHWAB_CLIENT_SECRET` | Yes | App secret from the same app. |
| `SCHWAB_CALLBACK_URL` | Yes | Must match a callback URL in the portal (HTTPS only), e.g. `https://127.0.0.1:8080/oauth/callback`. |
| `SCHWAB_REFRESH_TOKEN` | Optional* | From the one-time OAuth flow; or leave unset and use the token file (see below). |
| `SCHWAB_REFRESH_TOKEN_FILE` | No | Where to read/write the refresh token (default `data/.schwab_refresh_token`). App reads token from env first, then from this file. |
| `SCHWAB_BASE_URL` | No | Default `https://api.schwabapi.com`; only set if using a different base. |

\* You need a refresh token **either** in `SCHWAB_REFRESH_TOKEN` **or** in the token file. The get-refresh-token script writes to the file so you don’t have to edit `.env` each time.

### 1. Get a refresh token (one-time, then ~every 7 days)

The Schwab API uses OAuth. You need a **refresh token** before the app can get access tokens and fetch data. **Schwab requires callback URLs to be HTTPS** — that’s why `http://localhost:8765/callback` is rejected.

**You don’t have to manually paste the token into `.env`.** Run the script below; it writes the refresh token to `data/.schwab_refresh_token`. The app loads the token from that file automatically. Schwab refresh tokens last about **7 days**; when yours expires, run the script again (one browser sign-in) and the script will overwrite the file.

**You only need one callback URL.** If you already have `https://127.0.0.1:8080/oauth/callback` in the Developer Portal, use that. You do **not** need to add a second one (and you should not add `http://localhost:...` because it’s not HTTPS).

**Option A – Use your existing callback** (`https://127.0.0.1:8080/oauth/callback`)

1. In `.env` set:
   - `DATA_PROVIDER=schwab`
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
6. Open the URL printed, sign in, approve. The script saves the refresh token to `data/.schwab_refresh_token`; the app will use it automatically. You can add `SCHWAB_REFRESH_TOKEN` to `.env` only if you prefer.

### 2. Test the API with real data

After you have a refresh token (in `.env` or in the token file):

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
