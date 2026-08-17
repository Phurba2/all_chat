from django.core.management.base import BaseCommand

from inbox.gmail import fetch_emails, is_configured


class Command(BaseCommand):
    help = "Fetch recent unread Gmail emails into the inbox (idempotent)."

    def handle(self, *args, **options):
        if not is_configured():
            self.stderr.write("Set GMAIL_EMAIL and GMAIL_APP_PASSWORD environment variables first.")
            return
        new = fetch_emails()
        self.stdout.write(self.style.SUCCESS(f"Imported {new} new email(s)."))
