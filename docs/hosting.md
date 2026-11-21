# Hosting options

The current bot is a single-owner process that uses Telegram long polling. It has no HTTP
listener, and its writeups, export marker and reminder history live in a local SQLite file.
A host therefore needs a continuously running Python process, outbound HTTPS and persistent
writable storage.

## Staying within a zero hosting budget

An existing computer or spare Linux device can run this version with no hosting subscription.
It needs to stay powered on and connected to the internet for commands, digests and reminders
to work. The systemd setup in [ctfbot.service.example](ctfbot.service.example) is suitable for
a Linux machine you already have. Keep backups of the database on storage you control.

**Render's free web services do not fit the current architecture.** According to
[Render's free service documentation](https://render.com/docs/free):

- Background workers are unavailable on the free compute plan.
- Free web services sleep after 15 minutes without inbound traffic. Telegram long polling
  and outgoing CTFtime requests do not provide that inbound traffic.
- Local files, including SQLite databases, are lost on a restart, redeploy or idle shutdown.
- Free web services cannot attach a persistent disk.
- Free Render Postgres databases expire after 30 days, so they are unsuitable for a lasting
  writeup log.

A future free Render deployment would need a Telegram webhook HTTP service, a durable
external database and scheduling that can trigger digests and reminders while the web service
is asleep. Conversation drafts would also need to survive restarts. That requires a separate
implementation and accounts for the chosen storage and scheduling providers.

## Render requirements for the current version

If a paid plan becomes an option, the current architecture fits a
[background worker](https://render.com/docs/background-workers) with a
[persistent disk](https://render.com/docs/disks). Both require paid resources; these
instructions do not provision anything.

Configure a Python background worker from this repository:

| Setting | Value |
|---|---|
| Build command | `python -m pip install .` |
| Start command | `python -m ctfbot` |
| Disk mount path | `/var/data` |
| `DATABASE_PATH` | `/var/data/ctfbot.db` |
| `TELEGRAM_BOT_TOKEN` | Your token, entered privately in the Render Dashboard |
| `OWNER_ID` | Your numeric Telegram user ID |
| `TIMEZONE` | Your IANA timezone, for example `Africa/Kampala` |

The other settings use the defaults documented in the [README](../README.md#configuration).
If you want manual events or a custom category order, add them as Render secret files and set
`EVENTS_FILE` or `CATEGORIES_FILE` to the corresponding path under `/etc/secrets/`. Missing
optional files use CTFtime only and the built-in category order.

Find your owner ID locally with `python -m ctfbot --whoami` before starting the hosted bot.
Run only one polling instance per token: stop the local bot before starting the worker.
To move an existing log, stop the bot, back up the SQLite file, and transfer the database to
the disk mount path before starting the hosted instance. The database path must be a regular
file, rather than a directory or symlink.

After deployment, check that startup completes, `/start` answers your private chat, `/log`
saves an entry, and that the entry is still present after a worker restart. Confirm the
configured digest and reminder jobs from the worker's logs. Keep database backups outside the
service as well.
