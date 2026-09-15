from django.core.management.base import BaseCommand, CommandError

from inbox import scheduler


class Command(BaseCommand):
    help = "Fetch new Gmail messages for the server-configured account."

    def handle(self, *args, **options):
        try:
            new = scheduler.fetch_gmail_once()
        except Exception as e:
            raise CommandError(f"Gmail fetch failed: {e}")
        if new is None:
            raise CommandError(
                "Gmail is not configured server-side. Set GMAIL_EMAIL and "
                "GMAIL_APP_PASSWORD environment variables."
            )
        self.stdout.write(self.style.SUCCESS(f"Stored {new} new email(s)."))
