import os

from . import gmail, messenger

DEFAULT_INTERVAL_SECONDS = 60

FETCH_INTERVAL_ENV_VAR = "FETCH_INTERVAL_SECONDS"


def fetch_gmail_once():
    if not gmail.is_configured():
        return None
    return gmail.fetch_emails()


def fetch_messenger_once():
    if not messenger.is_configured():
        return None
    return messenger.fetch_messages(include_outbound=True)


def run_fetch_cycle():
    report = {"gmail": None, "messenger": None, "errors": []}
    for name, fetch in (("gmail", fetch_gmail_once),
                        ("messenger", fetch_messenger_once)):
        try:
            report[name] = fetch()
        except Exception as exc:
            report["errors"].append(f"{name} fetch failed: {exc}")
    return report
