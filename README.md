<a id="readme-top"></a>

<!-- PROJECT SHIELDS -->
[![Contributors][contributors-shield]][contributors-url]
[![Forks][forks-shield]][forks-url]
[![Stargazers][stars-shield]][stars-url]
[![Issues][issues-shield]][issues-url]
[![MIT License][license-shield]][license-url]

<!-- PROJECT HEADER -->
<br />
<div align="center">
  <h1 align="center">🚩 ctf-prep-bot</h1>

  <p align="center">
    A self-hosted Telegram bot that keeps one person ready for upcoming CTF competitions.
    <br />
    <a href="#getting-started"><strong>Get started »</strong></a>
    <br />
    <br />
    <a href="#usage">Usage</a>
    &middot;
    <a href="https://github.com/Antech-greyhat/ctf-prep-bot/issues/new?labels=bug">Report Bug</a>
    &middot;
    <a href="https://github.com/Antech-greyhat/ctf-prep-bot/issues/new?labels=enhancement">Request Feature</a>
  </p>
</div>

<!-- TABLE OF CONTENTS -->
<details>
  <summary>Table of Contents</summary>
  <ol>
    <li>
      <a href="#about-the-project">About The Project</a>
      <ul>
        <li><a href="#built-with">Built With</a></li>
      </ul>
    </li>
    <li>
      <a href="#getting-started">Getting Started</a>
      <ul>
        <li><a href="#prerequisites">Prerequisites</a></li>
        <li><a href="#connect-your-bot">Connect your bot</a></li>
      </ul>
    </li>
    <li><a href="#usage">Usage</a></li>
    <li><a href="#configuration">Configuration</a></li>
    <li><a href="#security-notes">Security notes</a></li>
    <li><a href="#development">Development</a></li>
    <li><a href="#roadmap">Roadmap</a></li>
    <li><a href="#contributing">Contributing</a></li>
    <li><a href="#license">License</a></li>
    <li><a href="#contact">Contact</a></li>
    <li><a href="#acknowledgments">Acknowledgments</a></li>
  </ol>
</details>

<!-- ABOUT THE PROJECT -->
## About The Project

Finding the next CTF to play means checking CTFtime, local community channels and event pages
by hand. ctf-prep-bot does that for you and sends the result to Telegram.

You run your own instance with your own bot token, and it answers **only you**.

Features:
* **`/events`** lists CTFs starting in the next 14 days (configurable). It merges
  [CTFtime](https://ctftime.org) with your own list of events that CTFtime does not have
  (for example CyberGame Kenya or Safaricom CTF), removes duplicates, sorts by start time and
  shows times in your timezone.
* **Weekly digest** of the same list on the day and time you choose.
* **`/digest`** sends the digest immediately.
* **`python -m ctfbot --whoami`** tells you your numeric Telegram user ID.

CTFtime events are filtered to **online, Jeopardy-format, open-to-everyone** CTFs. The filters
are constants at the top of [`src/ctfbot/ctftime.py`](src/ctfbot/ctftime.py).

There is no database, no Docker and no webhook. The bot uses long polling, so the machine
running it needs only outbound HTTPS.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

### Built With

* [![Python][python-shield]][python-url]
* [![python-telegram-bot][ptb-shield]][ptb-url]
* [![httpx][httpx-shield]][httpx-url]
* [![Ruff][ruff-shield]][ruff-url]
* [![Pytest][pytest-shield]][pytest-url]

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- GETTING STARTED -->
## Getting Started

### Prerequisites

* Python 3.11 or newer
* A Telegram account
* Git

### Connect your bot

1. **Create the bot.** Open [@BotFather](https://t.me/BotFather) in Telegram, send `/newbot`,
   choose a display name and a username ending in `bot`, and copy the token.
   **Treat the token like a password**: anyone who has it controls your bot.
2. **Harden it in BotFather.**
   * `/setjoingroups` → choose your bot → **Disable**, so nobody can add it to groups.
   * `/setcommands` → choose your bot → paste:
     ```text
     start - Greeting
     help - What this bot can do
     events - CTFs starting soon
     digest - Send the weekly digest now
     ```
3. **Clone and install.**
   ```sh
   git clone https://github.com/Antech-greyhat/ctf-prep-bot.git
   cd ctf-prep-bot
   python3 -m venv .venv
   source .venv/bin/activate          # Windows: .venv\Scripts\activate
   pip install .
   ```
4. **Create your `.env`.**
   ```sh
   cp .env.example .env
   chmod 600 .env                     # only you can read it (Linux/macOS)
   ```
   Open `.env` and set `TELEGRAM_BOT_TOKEN`. On Windows `chmod` does not apply; keep the file
   inside your user profile.
5. **Find your user ID.**
   ```sh
   python -m ctfbot --whoami
   ```
   Send your bot any message in a private chat. Your numeric ID and username are printed in
   the **terminal**, never in the chat. Check that the username is yours. The command exits
   after 120 seconds if no message arrives.
   (Alternative: [@userinfobot](https://t.me/userinfobot), a third-party bot.)
6. **Finish configuring.** Set `OWNER_ID` in `.env`, then:
   ```sh
   cp events.example.toml events.toml
   ```
   Replace the placeholder dates in `events.toml` with the real, confirmed dates, or delete
   entries you don't want. The bot validates this file at startup.
7. **Run it.**
   ```sh
   python -m ctfbot
   ```
8. **Verify.**
   * `/start` from your account gets a reply.
   * A second Telegram account gets **no reply at all**, and a warning with its user ID
     appears in the terminal.
   * `/events` lists upcoming events.
   * `/digest` sends the digest.
9. **Optional: run it 24/7 without Docker.** Use systemd with an unprivileged user,
   `WorkingDirectory` set to the repo, `EnvironmentFile` pointing to the 600-permission
   `.env`, and `Restart=on-failure`. A complete, hardened example with setup commands is in
   [`docs/ctfbot.service.example`](docs/ctfbot.service.example).
10. **If the token ever leaks:** send `/revoke` to @BotFather, put the new token in `.env` and
    restart the bot. Deleting a committed `.env` does **not** remove it from git history; the
    old token must be revoked.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- USAGE -->
## Usage

| Command | What it does |
|---|---|
| `/start` | Greeting |
| `/help` | Lists commands, the digest schedule and your timezone |
| `/events` | CTFs starting within `LOOKAHEAD_DAYS` |
| `/digest` | Sends the weekly digest now |

Example `/events` reply:

```text
Upcoming CTFs
Starting in the next 14 days. Times in Africa/Nairobi.

Securinets CTF Quals 2026
Sat 17 Oct 12:00 → Mon 19 Oct 00:00 EAT (36h)
CTFtime

Hack.lu CTF 2026
Fri 23 Oct 21:00 → Sun 25 Oct 21:00 EAT (2d)
CTFtime
```

To print upcoming CTFtime events in the terminal without Telegram:

```sh
python -m ctfbot.ctftime
```

### Manual events

`events.toml` holds CTFs that are not on CTFtime. See
[`events.example.toml`](events.example.toml) for the format. Dates must include a UTC offset,
for example `2026-11-07T09:00:00+03:00`. If the file does not exist, the bot shows CTFtime
events only. Restart the bot after editing it.

If a manual event has the same title (ignoring case and spacing) and start time as a CTFtime
event, the manual entry is shown.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- CONFIGURATION -->
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
without printing its value.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- SECURITY -->
## Security notes

The bot holds one secret (the bot token), accepts input from the internet (Telegram messages)
and displays data it does not control (CTFtime). This is what is protected and how.

### The bot token

* **Environment only.** `config.py` is the only module that reads `os.environ`; everything
  else receives a frozen `Config` object. `.env` is loaded with `override=False`, so on a
  server systemd's `EnvironmentFile` wins.
* **Never committed.** `.gitignore` covers `.env`, `events.toml` and `*.db`. A pre-commit hook
  runs [gitleaks](https://github.com/gitleaks/gitleaks) to block commits containing secrets.
  `.env.example` contains only placeholders.
* **Never logged or printed.**
  * The `token` field is `repr=False`, so it is not shown in `repr(config)` or tracebacks.
  * `httpx` and `httpcore` loggers are set to WARNING, because at INFO they log request URLs
    and Telegram API URLs contain the token.
  * python-telegram-bot's `InvalidToken` message includes the raw token. The bot catches it
    and prints its own message instead.
  * The log formatter replaces the token with `[REDACTED]` anywhere in a log line, including
    tracebacks.
* **Fail closed.** A missing or malformed token or `OWNER_ID` stops the bot at startup.
* **On disk.** `.env` should be `chmod 600`. Under systemd the bot runs as an unprivileged user
  in a hardened unit.

### Who can use the bot

* **Owner lock, two layers.** Every command handler uses
  `filters.User(user_id=OWNER_ID) & filters.ChatType.PRIVATE`, and a gatekeeper handler runs
  before all others and stops anything else. A test checks that every command is locked.
* **Silent rejection.** Strangers get no reply, so the bot does not confirm it exists. Only
  their numeric user ID is logged, never their message text.
* **Reduced surface.** The bot requests only `message` updates, cannot be added to groups, and
  always sends the digest to `OWNER_ID`.
* **`--whoami` never replies** in the chat and ignores messages queued before it started.

### Untrusted data

* **Validated.** Every CTFtime field is type-checked; malformed entries are skipped with a
  warning that logs only the event ID. `events.toml` is validated strictly at startup.
* **Escaped.** Messages use HTML parse mode and every external string goes through
  `html.escape`.
* **Safe links only.** Only `http://` and `https://` URLs are rendered, with a length cap.
  Link previews are disabled.
* **Clean text.** Terminal escape sequences, bidi overrides and zero-width characters are
  removed from titles.
* **Size limits.** Messages are split below Telegram's 4096-character limit, only between
  events, so HTML tags are never cut in half.

### Network behaviour

* One shared `httpx.AsyncClient` with a 10-second timeout and a User-Agent naming this project.
* CTFtime results are cached for 1 hour; a failed request is retried once with a backoff.
* If CTFtime is down, manual events are still shown with a short notice.
* The bot never scans or contacts CTF targets.

### Not protected

* Anyone with root or your user account on the host can read `.env`.
* Telegram bot chats are not end-to-end encrypted.
* If your Telegram account is compromised, so is access to the bot (which only reads public
  CTF data).
* Dependencies are pinned to exact versions but not hash-locked.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- DEVELOPMENT -->
## Development

```sh
pip install -e ".[dev]"
pre-commit install            # gitleaks + ruff before every commit
ruff check . && ruff format --check .
pytest                        # no network access needed
```

```text
src/ctfbot/
  __main__.py       entry point, fail-closed startup, --whoami switch
  config.py         the only module that reads environment variables
  models.py         Event dataclass and shared validators
  ctftime.py        CTFtime client and filters (no Telegram imports)
  manual_events.py  events.toml loading and validation
  sources.py        merge, de-duplicate, sort, lookahead window
  formatting.py     pure functions producing Telegram HTML
  handlers.py       owner lock and command handlers
  digest.py         weekly digest scheduling
  whoami.py         --whoami helper
  logging_setup.py  logging with token redaction
```

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- ROADMAP -->
## Roadmap

- [x] CTF event alerts from CTFtime and a manual events file
- [x] Weekly digest
- [ ] Writeup logging (SQLite)
- [ ] Weakness report by category
- [ ] Inactivity nudges

See the [open issues](https://github.com/Antech-greyhat/ctf-prep-bot/issues) for proposed
features and known issues.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- CONTRIBUTING -->
## Contributing

Contributions are welcome. If you have a suggestion, fork the repo and open a pull request, or
open an issue with the `enhancement` label.

1. Fork the project
2. Create your feature branch (`git checkout -b feat/amazing-feature`)
3. Run `ruff check .` and `pytest`
4. Commit your changes (`git commit -m "Add amazing feature"`)
5. Push to the branch (`git push origin feat/amazing-feature`)
6. Open a pull request

Never include a real bot token, `.env` or `events.toml` in a commit.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- LICENSE -->
## License

Distributed under the MIT License. See [`LICENSE`](LICENSE) for more information.

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- CONTACT -->
## Contact

Antech-greyhat - [github.com/Antech-greyhat](https://github.com/Antech-greyhat)

Project link: [https://github.com/Antech-greyhat/ctf-prep-bot](https://github.com/Antech-greyhat/ctf-prep-bot)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- ACKNOWLEDGMENTS -->
## Acknowledgments

* [CTFtime](https://ctftime.org) for the public events API
* [python-telegram-bot](https://python-telegram-bot.org)
* [Best-README-Template](https://github.com/othneildrew/Best-README-Template)
* [Shields.io](https://shields.io)

<p align="right">(<a href="#readme-top">back to top</a>)</p>

<!-- MARKDOWN LINKS & IMAGES -->
[contributors-shield]: https://img.shields.io/github/contributors/Antech-greyhat/ctf-prep-bot.svg?style=for-the-badge
[contributors-url]: https://github.com/Antech-greyhat/ctf-prep-bot/graphs/contributors
[forks-shield]: https://img.shields.io/github/forks/Antech-greyhat/ctf-prep-bot.svg?style=for-the-badge
[forks-url]: https://github.com/Antech-greyhat/ctf-prep-bot/network/members
[stars-shield]: https://img.shields.io/github/stars/Antech-greyhat/ctf-prep-bot.svg?style=for-the-badge
[stars-url]: https://github.com/Antech-greyhat/ctf-prep-bot/stargazers
[issues-shield]: https://img.shields.io/github/issues/Antech-greyhat/ctf-prep-bot.svg?style=for-the-badge
[issues-url]: https://github.com/Antech-greyhat/ctf-prep-bot/issues
[license-shield]: https://img.shields.io/github/license/Antech-greyhat/ctf-prep-bot.svg?style=for-the-badge
[license-url]: https://github.com/Antech-greyhat/ctf-prep-bot/blob/master/LICENSE
[python-shield]: https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white
[python-url]: https://www.python.org
[ptb-shield]: https://img.shields.io/badge/python--telegram--bot-22.8-26A5E4?style=for-the-badge&logo=telegram&logoColor=white
[ptb-url]: https://python-telegram-bot.org
[httpx-shield]: https://img.shields.io/badge/httpx-0.28-333333?style=for-the-badge
[httpx-url]: https://www.python-httpx.org
[ruff-shield]: https://img.shields.io/badge/Ruff-lint-D7FF64?style=for-the-badge&logo=ruff&logoColor=black
[ruff-url]: https://docs.astral.sh/ruff
[pytest-shield]: https://img.shields.io/badge/pytest-tested-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white
[pytest-url]: https://pytest.org
