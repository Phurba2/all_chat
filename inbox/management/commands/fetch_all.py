from django.core.management.base import BaseCommand

from inbox import scheduler


class Command(BaseCommand):
    help = "Run one fetch pass over every server-configured channel (Gmail and Messenger)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--quiet",
            action="store_true",
            help="Only print a line when something was fetched or an error occurred.",
        )

    def handle(self, *args, **options):
        quiet = options["quiet"]
        report = scheduler.run_fetch_cycle()

        for channel in ("gmail", "messenger"):
            new = report[channel]
            if new is None:
                if not quiet:
                    self.stdout.write(f"{channel}: not configured server-side, skipped.")
            elif new or not quiet:
                self.stdout.write(self.style.SUCCESS(f"{channel}: stored {new} new message(s)."))

        for error in report["errors"]:
            self.stdout.write(self.style.ERROR(error))
