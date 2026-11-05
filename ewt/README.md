# EWT Consensus Scan — data directory

Populated by the `ewt-email-scan` scheduled task
(`~/.claude/scheduled-tasks/ewt-email-scan/SKILL.md`).

**Status as of 2026-08-05: FIRST INGEST DONE, PARTIAL.**

Mail access works. 658 messages (2026-01-27 .. 2026-08-05) were
discovered and fetched with zero failures; 496 are analysis-bearing.
**30 have been extracted** — the 2026-07-21 .. 2026-08-05 current-state
window — producing 18 ticker files and `index.md`.

**466 analysis-bearing messages remain to be backfilled.** The full set
is roughly 360k tokens, which does not fit a single run, so the first
pass took the recent window because that is what determines live counts,
alternates and open trade ideas.

`state.json` still has `last_scanned_at: null` **on purpose** — setting
a watermark now would silently skip the backfill. The real watermark is
`processed_manifest.json` (`extracted_imap_ids` /
`pending_backfill_imap_ids`). `raw/` is append-only, so re-running never
duplicates and never skips.

One finding worth knowing: **there are zero attachments in the entire
corpus.** Charts are hosted links on elliottwavetrader.net, not files,
so the PDF/XLSX/chart-image handling in the task spec does not apply to
this source. Chart labels survive as `<chart: label>` markers in the
extracted text.

## History: why it was blocked until 2026-08-05

The scheduled task runs in a non-interactive session that has no path
to the inbox:

| Route | Status |
|---|---|
| Gmail / mail MCP connector | No mail tools reach this session. `ToolSearch "+gmail"` returns nothing; `claude mcp list` shows only `plugin:stripe:stripe`; no `mcpServers` in any settings file. A Google **Drive** server (`b52e5956…`) is present but exposes file tools only — no message/thread/label tools. NOTE: the connector may be connected on claude.ai and still be invisible here — claude.ai connectors do not load into a local Claude Code scheduled-task run |
| Local Mail.app store (`~/Library/Mail`) | `Operation not permitted` — macOS TCC blocks it (needs Full Disk Access) |
| Mail CLI (`himalaya`, `mutt`, `notmuch`, `mbsync`, …) | None installed |
| Claude-in-Chrome (real logged-in Chrome) | No browser extension connected |
| In-app Browser pane | Fresh profile, not signed in; entering credentials is prohibited |
| Google Drive | Searched for `elliottwavetrader` / `EWT` / `Gilburt` — zero hits |

## To unblock

Any **one** of these is enough:

1. ~~Connect a Gmail connector in claude.ai connector settings.~~
   **RULED OUT 2026-08-05 — closed by platform design, not fixable by
   reconnecting.** Evidence from `~/.claude.json`:
   - `tengu_claudeai_mcp_connectors: true` — the claude.ai→Claude Code
     connector bridge *is* enabled here.
   - `claudeAiMcpEverConnected: ["claude.ai Google Drive"]` — Drive is
     the only connector ever bridged in. That is exactly why this
     session has Drive file tools and no mail tools.
   - `tengu_mcp_local_oauth_blocked_hosts.hosts` contains
     `gmail.mcp.claude.com`, `gcal.mcp.claude.com`,
     `microsoft365.mcp.claude.com`. Gmail/Calendar/M365 are blocked
     from completing local OAuth in Claude Code. Drive is **not** on
     that list — a clean natural experiment explaining the split.
   - Adding it by hand is also a dead end: `gmail.mcp.claude.com`
     resolves (CNAME → `ghs.googlehosted.com`) but serves no usable
     endpoint — `curl` fails the TLS handshake (HTTP 000) while
     control probes to `api.anthropic.com` / `mcp.stripe.com` return
     404 / 401, so outbound networking is fine. There is no standalone
     remote MCP URL to point `claude mcp add` at.
2. Keep Chrome running with the Claude extension connected and signed
   in to webmail — works, but only while that Chrome session is alive,
   so it is a poor fit for an unattended scheduled task.
3. ~~Grant Full Disk Access so `~/Library/Mail` is readable.~~
   **Likely useless here:** the `com.apple.mail` container exists but
   `defaults read com.apple.mail` shows zero account/IMAP keys and
   Mail.app is not running — this mail does not appear to be synced
   locally. Only worth trying if Mail.app is actually set up with the
   account.
4. **Mail CLI (`himalaya`) + Google app password — the chosen route.**
   Installed and configured 2026-08-05. See below; one manual step
   remains.

## Mail access: himalaya (IMAP)

`himalaya` v2.0.0 is installed (`/opt/homebrew/bin/himalaya`) and
configured at `~/.config/himalaya/config.toml`.

The config declares a single read-only IMAP account. **There is no SMTP
block on purpose** — the scan only reads, so the tooling is structurally
incapable of sending mail. The app password is never stored on disk: the
config resolves it at call time from the macOS Keychain via
`password.command`, so the secret stays out of dotfiles, git, and agent
transcripts.

Verified working: `himalaya account list` parses the config and reports
the `gmail` account on the `imap` backend, and `himalaya account check`
reaches the password step — the *only* reported failure is the missing
Keychain item.

### Remaining manual step (account owner only)

1. Generate a Google **app password** at
   <https://myaccount.google.com/apppasswords> (requires 2-step
   verification). Gmail rejects the plain account password over SASL
   PLAIN, so it must be an app password.
2. Store it in the Keychain — this prompts for the secret, so nothing is
   echoed or logged:

   ```
   security add-generic-password -a "$USER" -s himalaya-gmail -w
   ```

3. Confirm: `himalaya account check` should report `imap: OK`.

If EWT mail arrives at an address other than the one in
`imap.sasl.plain.username`, edit that field first.

### Commands the scan uses

Search full history (All Mail, oldest first — the first run must process
everything, not just the inbox):

```
himalaya envelope search -m archive --json 'from elliottwavetrader.net order by date asc'
```

Read one message, then pull its attachments:

```
himalaya message read <ID> -m archive --json
himalaya attachment download <ID> -m archive --dir <path>
```

Query DSL conditions: `date`/`after <yyyy-mm-dd>`, `from`, `to`,
`subject`, `body`, `flag`, combined with `and`/`or`/`not` and
parenthesised groups; `order by <date|from|to|subject> [asc|desc]`.
Text matching is case-insensitive substring against the `Date:`-header
sent-at ordering.

## Layout

```
ewt/
  state.json          scan watermark + last-run status
  raw/                <date>-<msgid>.json — append-only per-message extractions
  tickers/            <SYMBOL>.md — one file per ticker, append-only change log
  index.md            one row per ticker, sorted by most recently updated
```

`index.md` is not created until there is at least one ticker to list.
