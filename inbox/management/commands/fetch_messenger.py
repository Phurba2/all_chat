from django.core.management.base import BaseCommand, CommandError

from inbox.messenger import fetch_messages


class Command(BaseCommand):
    help = (
        "Backfill inbound Messenger messages from the Graph API. The webhook is "
        "the normal receive path; this catches up on messages delivered while the "
        "webhook was down or not yet subscribed."
    )

    def handle(self, *args, **options):
        try:
            new = fetch_messages()
        except RuntimeError as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS(f"Stored {new} new Messenger message(s)."))
