# Scripts

## Schwab API (real data)

### 1. Get a refresh token (one-time)

The Schwab API uses OAuth. You need a **refresh token** before the app can get access tokens and fetch data.

1. In the [Schwab Developer Portal](https://developer.schwab.com/), add this callback URL to your app:  
   `http://localhost:8765/callback`
2. In `.env` set:
   - `SCHWAB_CLIENT_ID` (your app key)
   - `SCHWAB_CLIENT_SECRET` (your app secret)
3. Run:
   ```bash
   poetry run python scripts/schwab_get_refresh_token.py
   ```
4. Open the URL printed in your browser, sign in to Schwab, and approve the app.
5. The script will print a line like `SCHWAB_REFRESH_TOKEN=...`. Add that to your `.env`.

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
