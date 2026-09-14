from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("inbox", "0010_fix_created_at_editable"),
    ]

    operations = [
        migrations.AlterField(
            model_name="message",
            name="user_email",
            field=models.CharField(blank=True, default="", max_length=320),
        ),
        migrations.AlterField(
            model_name="message",
            name="channel",
            field=models.CharField(choices=[("whatsapp", "WhatsApp"), ("messenger", "Messenger"), ("email", "Gmail")], max_length=20),
        ),
        migrations.AlterField(
            model_name="message",
            name="contact",
            field=models.CharField(max_length=320),
        ),
        migrations.AlterField(
            model_name="message",
            name="subject",
            field=models.CharField(blank=True, default="", max_length=998),
        ),
        migrations.AlterField(
            model_name="message",
            name="message_id",
            field=models.CharField(blank=True, default="", max_length=512),
        ),
        migrations.AlterModelOptions(
            name="message",
            options={"ordering": ["created_at", "pk"]},
        ),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(fields=["user_email", "channel", "contact"], name="inbox_messa_user_em_7c6a2b_idx"),
        ),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(fields=["channel", "message_id"], name="inbox_messa_channel_0d3b4f_idx"),
        ),
        migrations.AddIndex(
            model_name="message",
            index=models.Index(fields=["created_at"], name="inbox_messa_created_9f2d6e_idx"),
        ),
        migrations.AddConstraint(
            model_name="message",
            constraint=models.UniqueConstraint(
                condition=~Q(message_id=""),
                fields=("channel", "user_email", "message_id"),
                name="unique_external_message",
            ),
        ),
    ]
