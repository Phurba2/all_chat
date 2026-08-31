from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from inbox.models import Message

# (channel, contact, subject, text)
SAMPLE = [
    ("whatsapp", "Priya Sharma", "", "Hey! Is the blue hoodie in size M still available?"),
    ("whatsapp", "Priya Sharma", "", "Great, I'll take it. Can you ship to Mumbai?"),
    ("messenger", "Tom Baker", "", "What time does the store close on Sundays?"),
    ("messenger", "Lisa Nguyen", "", "Sent you a voice note about the event."),
    ("email", "sarah@example.com", "Refund request for order #4821", "Hi, I'd like a refund for order #4821. Thanks!"),
    ("email", "sarah@example.com", "Re: Refund request for order #4821", "Invoice attached below for reference."),
]


class Command(BaseCommand):
    help = "Seed the inbox with sample conversations"

    def handle(self, *args, **options):
        if Message.objects.exists():
            self.stdout.write("Messages already exist — skipping (delete db.sqlite3 to reseed).")
            return
        now = timezone.now()
        for i, (channel, contact, subject, text) in enumerate(SAMPLE):
            Message.objects.create(
                channel=channel,
                contact=contact,
                subject=subject,
                text=text,
                created_at=now - timedelta(hours=len(SAMPLE) - i),
                is_read=(i % 3 != 0),
            )
        # a couple of outgoing replies so threads feel real
        Message.objects.create(channel="whatsapp", contact="Priya Sharma", direction="out",
                               text="Yes, size M is in stock — ₹1,499 shipped free to Mumbai. Confirm?", is_read=True)

        self.stdout.write(self.style.SUCCESS(f"Seeded {Message.objects.count()} messages."))
