# Unified Inbox

A minimal Django app that pulls Gmail and Facebook Messenger messages into a single dashboard.

## Quickstart

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Open http://127.0.0.1:8000

## Configuration

All credentials are configured server-side via environment variables (or a `.env` file — loaded automatically).

Gmail (requires an app password):

```bash
export GMAIL_EMAIL=you@gmail.com
export GMAIL_APP_PASSWORD=your-app-password
```

Messenger:

```bash
export META_PAGE_ID=your-page-id
export META_PAGE_ACCESS_TOKEN=your-page-access-token
export META_APP_SECRET=your-app-secret
export META_VERIFY_TOKEN=something-you-create
export META_GRAPH_VERSION=v21.0
```

Messenger credentials can also be stored in a gitignored `messenger_server_config.json` (saved values take precedence over env vars).

## Fetching Messages

### Automated

One pass over all configured channels — safe to run repeatedly, duplicates are skipped:

```bash
.venv/bin/python manage.py fetch_all
.venv/bin/python manage.py fetch_all --quiet
```

Cron example:

```bash
*/5 * * * * cd /path/to/unified-inbox && .venv/bin/python manage.py fetch_all --quiet
```

Or a long-running poller, no cron needed:

```bash
.venv/bin/python manage.py run_scheduler                 # every FETCH_INTERVAL_SECONDS (default 60)
.venv/bin/python manage.py run_scheduler --interval 30   # override per-run
```

Run it under a process manager (systemd, supervisord, tmux) in production. Individual channels can also be fetched with `fetch_gmail` and `fetch_messenger`.

### Real-time (Messenger)

Meta delivers incoming Messenger messages to the webhook (needs to be publicly reachable over HTTPS):

```text
https://your-domain.com/messenger/webhook/
```

Register that URL with the `verify_token` above in your Meta app's Messenger webhook settings.

## Replying

Open a conversation at `/channel/<channel>/<contact>/` and use the reply bar. Email replies go via SMTP, Messenger replies via the Graph API Send API.

## Project Structure

- `inbox/models.py` — Message model
- `inbox/views.py` — Conversation list & thread views, Messenger webhook
- `inbox/gmail.py` — IMAP fetch and SMTP reply
- `inbox/messenger.py` — Graph API fetch, send, webhook verification
- `inbox/scheduler.py` — Server-side automated fetch cycle
- `templates/` — HTML templates

## License

MIT
