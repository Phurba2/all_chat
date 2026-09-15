import os
import time

from django.core.management.base import BaseCommand, CommandError

from inbox import scheduler


class Command(BaseCommand):
    help = "Fetch new Gmail and Messenger messages every FETCH_INTERVAL_SECONDS (default 60) until stopped."

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=None,
            help="Seconds between fetch cycles (overrides FETCH_INTERVAL_SECONDS).",
        )

    def handle(self, *args, **options):
        interval = options["interval"]
        if interval is None:
            try:
                interval = int(os.environ.get(
                    scheduler.FETCH_INTERVAL_ENV_VAR, scheduler.DEFAULT_INTERVAL_SECONDS))
            except ValueError:
                raise CommandError(
                    f"{scheduler.FETCH_INTERVAL_ENV_VAR} must be an integer number of seconds."
                )
        if interval <= 0:
            raise CommandError("Interval must be a positive number of seconds.")

        while True:
            report = scheduler.run_fetch_cycle()
            for error in report["errors"]:
                self.stdout.write(self.style.ERROR(error))
            for channel in ("gmail", "messenger"):
                new = report[channel]
                if new:
                    self.stdout.write(self.style.SUCCESS(f"{channel}: stored {new} new message(s)."))
            time.sleep(interval)
