# Scripts

## Schwab API (real data)

**Credentials to test the Schwab data provider** — set these in `.env`:

| Variable | Required | Description |
|----------|----------|-------------|
| `DATA_PROVIDER` | Yes | Set to `schwab` or `thinkorswim` so the app uses Schwab. |
| `SCHWAB_CLIENT_ID` | Yes | App key (Client ID) from Schwab Developer Portal. |
| `SCHWAB_CLIENT_SECRET` | Yes | App secret from the same app. |
| `SCHWAB_CALLBACK_URL` | Yes | HTTPS callback: **Option A** use a local URL (e.g. `https://127.0.0.1:8080/oauth/callback`) and paste the redirect URL; **Option B** use your ngrok URL + `/callback` for automatic callback. |
| `SCHWAB_REFRESH_TOKEN` | Optional* | From the one-time OAuth flow; or leave unset and use the token file (see below). |
| `SCHWAB_REFRESH_TOKEN_FILE` | No | Where to read/write the refresh token (default `data/.schwab_refresh_token`). App reads token from env first, then from this file. |
| `SCHWAB_BASE_URL` | No | Default `https://api.schwabapi.com`; only set if using a different base. |

\* You need a refresh token **either** in `SCHWAB_REFRESH_TOKEN` **or** in the token file. The get-refresh-token script writes to the file so you don’t have to edit `.env` each time.

### 1. Get a refresh token (one-time, then ~every 7 days)

The Schwab API uses OAuth. You need a **refresh token** before the app can get access tokens and fetch data. Schwab requires an **HTTPS** callback URL. You can use either option below.

**Option A – Paste URL (no ngrok)**

No extra tools. Use a local callback URL; after sign-in you paste the redirect URL into the script.

1. In the [Schwab Developer Portal](https://developer.schwab.com/), add callback URL: **`https://127.0.0.1:8080/oauth/callback`**
2. In `.env` set: `DATA_PROVIDER=schwab`, `SCHWAB_CLIENT_ID`, `SCHWAB_CLIENT_SECRET`, **`SCHWAB_CALLBACK_URL=https://127.0.0.1:8080/oauth/callback`**
3. Run: **`poetry run python scripts/schwab_get_refresh_token.py`**
4. Sign in when the browser opens. The browser will redirect and show "site can't be reached" — **copy the full URL from the address bar** (it contains `?code=...`).
5. Paste that URL when the script asks. The script will exchange the code and save the token to `data/.schwab_refresh_token`.

**Option B – Ngrok (automatic callback)**

The redirect loads a success page and the script receives the token without pasting.

1. Install [ngrok](https://ngrok.com/) and run: **`ngrok http 8765`**
2. Copy the **HTTPS** URL ngrok shows (e.g. `https://abc123.ngrok-free.app`).
3. In the [Schwab Developer Portal](https://developer.schwab.com/), add: **`https://YOUR_NGROK_URL/callback`**
4. In `.env` set: `DATA_PROVIDER=schwab`, `SCHWAB_CLIENT_ID`, `SCHWAB_CLIENT_SECRET`, **`SCHWAB_CALLBACK_URL=https://YOUR_NGROK_URL/callback`**
5. Leave ngrok running and run: **`poetry run python scripts/schwab_get_refresh_token.py`**
6. Sign in when the browser opens. You'll see "Authorization successful" and the script will save the token.

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
