# ctf-prep-bot

A small, self-hosted Telegram bot that helps **one person** prepare for CTF competitions.
You run your own instance with your own bot token; it answers only you.

**v1: CTF event alerts.**

- `/events` lists CTFs starting in the next 14 days (configurable). It merges
  [CTFtime](https://ctftime.org) with your own list of events that CTFtime does not have
  (for example CyberGame Kenya or Safaricom CTF), removes duplicates, sorts by start time and
  shows times in your timezone.
- A weekly digest of the same list arrives on the day and time you choose.
- `/digest` sends the digest immediately, which is handy for testing.
- `python -m ctfbot --whoami` tells you your numeric Telegram user ID.

CTFtime events are filtered to **online, Jeopardy-format, open-to-everyone** CTFs. The filters
are documented constants at the top of [`src/ctfbot/ctftime.py`](src/ctfbot/ctftime.py).

Requires Python 3.11+. No database, no Docker, no webhooks: it uses long polling, so the
machine running it needs only outbound HTTPS.

## Connect your bot

1. **Create the bot.** Open [@BotFather](https://t.me/BotFather) in Telegram, send `/newbot`,
   choose a display name and a username ending in `bot`, and copy the token it gives you.
   **Treat the token like a password**: anyone who has it controls your bot.
2. **Harden it in BotFather.**
   - `/setjoingroups` → choose your bot → **Disable**, so nobody can add it to groups.
   - `/setcommands` → choose your bot → paste:
     ```
     start - Greeting
     help - What this bot can do
     events - CTFs starting soon
     digest - Send the weekly digest now
     ```
3. **Install.**
   ```bash
   git clone https://github.com/Antech-greyhat/ctf-prep-bot.git
   cd ctf-prep-bot
   python3 -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   pip install .
   ```
4. **Create your `.env`.**
   ```bash
   cp .env.example .env
   chmod 600 .env                     # only you can read it (Linux/macOS)
   ```
   Open `.env` and set `TELEGRAM_BOT_TOKEN`. On Windows, `chmod` does not apply; keep the
   file inside your user profile instead.
5. **Find your user ID.**
   ```bash
   python -m ctfbot --whoami
   ```
   Send your bot any message in a private chat. Your numeric ID and username are printed in
   the **terminal** (never in the chat). Check that the username is yours: if someone else
   messaged the bot first, you would see their ID. The command exits after 120 seconds if no
   message arrives.
   (Alternative: [@userinfobot](https://t.me/userinfobot), a third-party bot.)
6. **Finish configuring.** Set `OWNER_ID` in `.env`. Then:
   ```bash
   cp events.example.toml events.toml
   ```
   and replace the placeholder dates in `events.toml` with the real, confirmed dates (or
   delete entries you don't want). The bot validates this file at startup.
7. **Run it.**
   ```bash
   python -m ctfbot
   ```
8. **Verify.**
   - `/start` from your account gets a reply.
   - A second Telegram account gets **no reply at all**, and a warning with that account's
     user ID appears in the terminal.
   - `/events` lists upcoming events.
   - `/digest` sends the digest.
9. **Optional: run it 24/7 without Docker.** Use systemd with an unprivileged user,
   `WorkingDirectory` set to the repo, `EnvironmentFile` pointing to the 600-permission `.env`,
   and `Restart=on-failure`. A complete, hardened example with setup commands is in
   [`docs/ctfbot.service.example`](docs/ctfbot.service.example).
10. **If the token ever leaks:** send `/revoke` to @BotFather to invalidate it, put the new
    token in `.env`, and restart the bot. Deleting a committed `.env` does **not** remove it
    from git history; the old token must be revoked.

## Configuration

All settings are environment variables, usually set in `.env`. Real environment variables
take precedence over `.env`.

| Variable | Required | Default | Meaning |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | | Token from @BotFather |
| `OWNER_ID` | yes | | Your numeric Telegram user ID (positive integer) |
| `TIMEZONE` | no | `Africa/Nairobi` | IANA timezone for display and the digest schedule |
| `LOOKAHEAD_DAYS` | no | `14` | Show CTFs starting within this many days (1-365) |
| `DIGEST_DAY` | no | `mon` | `mon` `tue` `wed` `thu` `fri` `sat` `sun` |
| `DIGEST_TIME` | no | `08:00` | 24-hour `HH:MM` in `TIMEZONE` |
| `EVENTS_FILE` | no | `events.toml` | Manual events file, relative to the working directory |

If anything required is missing or malformed, the bot refuses to start and names the variable
(without printing its value).

### Manual events

`events.toml` holds CTFs that are not on CTFtime. See
[`events.example.toml`](events.example.toml) for the format. Dates must include a UTC offset,
for example `2026-11-07T09:00:00+03:00`, so there is no doubt which moment is meant. If the file
does not exist, the bot runs with CTFtime events only. Restart the bot after editing it.

If a manual event has the same title (ignoring case and spacing) and start time as a CTFtime
event, the manual entry is shown.

## Development

```bash
pip install -e ".[dev]"
pre-commit install            # gitleaks + ruff run before every commit
ruff check . && ruff format --check .
pytest                        # no network access needed
python -m ctfbot.ctftime      # print upcoming CTFtime events (UTC), no Telegram needed
```

Layout:

```
src/ctfbot/
  __main__.py       entry point: builds the app, fail-closed startup, --whoami switch
  config.py         the only module that reads environment variables
  models.py         Event dataclass and shared validators (URL, text, timestamps)
  ctftime.py        CTFtime client, filters, parsing (no Telegram imports)
  manual_events.py  events.toml loading and validation
  sources.py        merges sources, de-duplicates, sorts, applies the lookahead window
  formatting.py     pure functions producing Telegram HTML, message splitting
  handlers.py       owner lock and thin command handlers
  digest.py         weekly digest scheduling
  whoami.py         --whoami helper
  logging_setup.py  logging config with token redaction
```

## Security notes

This bot holds one secret (the bot token), accepts input from the internet (Telegram messages)
and renders data it does not control (CTFtime). Here is what is protected and how.

### The bot token

- **Environment only.** The token comes only from `TELEGRAM_BOT_TOKEN`. `config.py` is the only
  module that reads `os.environ`; everything else receives a frozen `Config` object.
  `python-dotenv` loads `.env` with `override=False`, so on a server systemd's `EnvironmentFile`
  wins.
- **Never committed.** `.gitignore` covers `.env` (and `.env.*` except `.env.example`),
  `events.toml` and `*.db`. A pre-commit hook runs [gitleaks](https://github.com/gitleaks/gitleaks)
  to block commits containing secrets. `.env.example` contains only placeholders.
- **Never logged or printed.**
  - The `token` field is `repr=False`, so it does not appear in `repr(config)` or tracebacks.
  - `httpx` and `httpcore` loggers are set to WARNING: at INFO, httpx logs every request URL,
    and Telegram API URLs contain the token.
  - python-telegram-bot's own `InvalidToken` error message includes the raw token. The bot
    catches it and prints a generic message instead.
  - As a final layer, the log formatter replaces the token with `[REDACTED]` anywhere in a log
    line, including tracebacks. This was verified by starting the bot with a fake token:
    the library's traceback was logged with the token redacted.
- **Fail closed.** A missing or malformed token or `OWNER_ID` stops the bot at startup with a
  message naming the variable, never its value.
- **On disk.** `.env` should be `chmod 600`. Under systemd, the bot runs as an unprivileged
  user with a hardened unit (read-only filesystem, no new privileges, no capabilities).

### Who can use the bot

- **Owner lock, two layers.** Every command handler has the filter
  `filters.User(user_id=OWNER_ID) & filters.ChatType.PRIVATE`. In addition, a gatekeeper
  handler runs before all others and stops any update that is not from the owner in a private
  chat. A test checks that every registered command carries the owner filter.
- **Silent rejection.** Strangers get no reply at all, so the bot does not confirm it exists or
  is alive. Only their numeric user ID is logged, at WARNING. Their message text is never
  logged.
- **Reduced surface.** The bot asks Telegram for `message` updates only, and BotFather's
  `/setjoingroups → Disable` prevents it being added to groups. The digest is always sent to
  `OWNER_ID`, never to whoever triggered it.
- **`--whoami` never replies** in the chat, and ignores messages queued before it started, so
  a stale message from someone else cannot be mistaken for yours. Still, check the printed
  username.

### Untrusted data (CTFtime and events.toml)

- **Validated.** Every CTFtime field is type-checked. Malformed entries are skipped with a
  warning that logs only the numeric event ID, never field values (which could inject fake
  log lines). `events.toml` is validated strictly at startup, including unknown keys.
- **Escaped.** Messages use HTML parse mode, and every external string is passed through
  `html.escape` (including `quote=True` inside `href`), so titles cannot inject markup.
- **Safe links only.** Only absolute `http://` and `https://` URLs are rendered, capped in
  length; `javascript:`, `data:` and others are dropped. Link previews are disabled, so
  Telegram never fetches an untrusted URL on your behalf.
- **Clean text.** Control and format characters (terminal escape sequences, bidi overrides,
  zero-width characters) are removed from titles, which matters because the standalone mode
  and `--whoami` print to a terminal.
- **Size limits.** Titles and URLs are length-capped, and messages are split below Telegram's
  4096-character limit (counted in UTF-16 units, as Telegram does) only at block boundaries,
  so HTML tags are never cut in half.

### Network behaviour

- One shared `httpx.AsyncClient` for CTFtime with a 10-second timeout and a User-Agent naming
  this project.
- Results are cached in memory for 1 hour, and a failed request is retried at most once with
  a backoff, to be polite to CTFtime.
- If CTFtime is down, the bot still shows your manual events with a short "CTFtime is
  unavailable" notice.
- The bot never scans or contacts CTF targets; it only talks to Telegram and CTFtime.

### What this does not protect against

- Anyone with root or your user account on the host can read `.env`.
- Telegram bot chats are not end-to-end encrypted; Telegram's servers can see the messages.
- The owner lock relies on Telegram's sender identity. If your Telegram account is
  compromised, so is access to the bot (which only reads public CTF data).
- Dependencies are pinned to exact versions but not hash-locked.

## License

MIT, see [LICENSE](LICENSE).
