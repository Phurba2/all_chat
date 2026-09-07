import os

from django.db import migrations


def assign_legacy_messenger_messages(apps, schema_editor):
    """Re-attribute old server-level Messenger messages to the env Page.

    Before Messenger became session/per-Page (like Gmail), messages were
    stored with user_email="". Attribute them to the Page the server is
    configured for (META_PAGE_ID) so they remain visible after a session
    connects that same Page. When no Page is configured, leave them as-is.
    """
    Message = apps.get_model("inbox", "Message")
    page_id = os.environ.get("META_PAGE_ID")
    if page_id:
        Message.objects.filter(channel="messenger", user_email="").update(user_email=str(page_id))


class Migration(migrations.Migration):

    dependencies = [
        ("inbox", "0007_alter_message_options_message_created_at"),
    ]

    operations = [
        migrations.RunPython(assign_legacy_messenger_messages, migrations.RunPython.noop),
    ]
