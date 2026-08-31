# Unified Inbox

A minimal Django app that pulls messages from WhatsApp, Messenger, Gmail, and TikTok into a single dashboard.

## Quickstart

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Open http://127.0.0.1:8000

## Gmail Setup

```bash
export GMAIL_EMAIL=you@gmail.com
export GMAIL_APP_PASSWORD=your-app-password
.venv/bin/python manage.py fetch_gmail
```

## Project Structure

- `inbox/models.py` — Message model
- `inbox/views.py` — Conversation list & thread views
- `templates/` — HTML templates
- `docs/` — GitHub Pages (Terms, Privacy)

## Legal

- [Terms of Service](https://phurba2.github.io/all_chat/terms.html)
- [Privacy Policy](https://phurba2.github.io/all_chat/privacy.html)

## License

MIT
